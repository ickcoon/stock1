"""Offline Step 7-B2-A probes; all HTTP responses are explicitly synthetic."""
from copy import deepcopy
from datetime import datetime, timedelta
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from kiwoom_collector import daily_chart as daily
from kiwoom_collector import daily_diagnostic as probe
from kiwoom_collector.client import KST, Token
from kiwoom_collector.config import Credentials
from kiwoom_collector.errors import KiwoomError
from test_leader_tracking import snapshot, calendar

NOW = datetime(2026,10,29,16,tzinfo=KST)
FIXTURE = json.loads((Path(__file__).parent/'fixtures/daily_chart_synthetic.json').read_text(encoding='utf-8'))
APP, SECRET, TOKEN = 'TEST_APP_KEY_NOT_REAL', 'TEST_SECRET_KEY_NOT_REAL', 'TEST_ACCESS_TOKEN_NOT_REAL'


def req(code='005930',api='ka10081',environment='mock',**kwargs):
    return daily.DailyRequest(api,code,environment,kwargs.get('base_dt','20261028'),kwargs.get('adjustment','0'))


def payload(request=None,rows=None):
    request = request or req()
    return dict(return_code=0,return_msg='SYNTHETIC response, not measured',stk_cd=request.stock_code,
                **{daily.LIST_KEYS[request.api_id]:deepcopy(FIXTURE['rows'] if rows is None else rows)})


def response(body=None,status=200,headers=None):
    out = Mock()
    out.status_code = status
    out.headers = {'cont-yn':'N', **(headers or {})}
    out.json.return_value = payload() if body is None else body
    return out


def inspect(body=None,request=None,headers=None,**kwargs):
    request = request or req()
    return probe.inspect_response(payload(request) if body is None else body,request=request,
        started_at=NOW,received_at=NOW+timedelta(seconds=1),http_status=kwargs.get('status',200),
        headers=headers or {'api-id':request.api_id,'cont-yn':'N'},redactor=probe.Redactor(APP,SECRET,TOKEN))


class DiagnosticParserTests(unittest.TestCase):
    def test_both_b1_array_keys_and_no_final_close_claim(self):
        for api in daily.LIST_KEYS:
            report = inspect(request=req(api=api))
            self.assertTrue(report['b1_parse_success'])
            self.assertTrue(report['b1_validated'])
            self.assertEqual(report['ordering'],'descending')
            self.assertEqual(report['rows'][0]['trade_date'],'2026-10-28')
            self.assertEqual(report['rows'][0]['ohlc']['close'],'106')
            self.assertEqual(report['rows'][0]['close_status'],'cur_prc_candidate_not_verified_final_close')
            self.assertFalse(any(row['eligible_for_tracking'] for row in report['rows']))
            self.assertEqual(report['tracking']['observations_generated'],0)

    def test_missing_volume_amount_and_units_not_reconstructed(self):
        body = payload()
        del body[daily.LIST_KEYS['ka10081']][0]['trde_prica']
        del body[daily.LIST_KEYS['ka10081']][0]['trde_qty']
        report = inspect(body)
        self.assertEqual(report['status'],'parsed_with_issues')
        self.assertIsNone(report['rows'][0]['turnover_won'])
        self.assertIsNone(report['rows'][0]['volume'])
        self.assertIn('missing:trde_prica',report['rows'][0]['issues'])
        self.assertEqual(report['units']['status'],'documented_not_runtime_verified')

    def test_price_sign_and_adjustment_metadata_preserved(self):
        body = payload()
        row = body[daily.LIST_KEYS['ka10081']][0]
        row.update(upd_stkpc_tp='256',upd_rt='-50.00')
        report = inspect(body,request=req(adjustment='1'))
        self.assertEqual(report['adjustment_request'],'1')
        self.assertEqual(report['rows'][0]['raw']['upd_stkpc_tp'],'256')
        self.assertEqual(report['rows'][0]['raw']['upd_rt'],'-50.00')
        self.assertEqual(report['rows'][0]['price_signs']['low_pric'],'-')
        self.assertEqual(report['rows'][0]['ohlc']['low'],'96')
        self.assertEqual(report['rows'][0]['turnover_won'],'125000.000')

    def test_reversed_duplicate_and_future_dates_remain_quarantined(self):
        key = daily.LIST_KEYS['ka10081']
        for rows,expected in ((list(reversed(FIXTURE['rows'])),'daily_order_unverified'),
                              ([FIXTURE['rows'][0],FIXTURE['rows'][0]],'duplicate_daily_dates')):
            result = inspect(payload(rows=rows))
            self.assertFalse(result['b1_validated'])
            self.assertIn(expected,result['page_issues'])
        body = payload(); body[key][0]['dt']='20261030'
        result = inspect(body)
        self.assertFalse(result['b1_validated'])
        self.assertIn('future_or_after_base_date',result['rows'][0]['issues'])
        self.assertFalse(result['rows'][0]['eligible_for_tracking'])

    def test_http_api_code_and_sanitized_cause_survive_failure(self):
        for status in (200,429,500):
            report = inspect({'return_code':1700,'return_msg':'허용된 요청 개수 초과'},status=status)
            self.assertEqual(report['return_code'],1700)
            self.assertEqual(report['http_status'],status)
            self.assertEqual(report['return_msg'],'허용된 요청 개수 초과')
            self.assertIn(report['status'],('http_error','api_error'))
        self.assertEqual(inspect({'return_code':8104,'return_msg':'모의투자 미지원'})['return_code'],8104)

    def test_wrong_code_array_api_and_malformed_response_are_rejected(self):
        for body,headers in ((dict(payload(),stk_cd='000660'),None),
                             ({'return_code':0,'stk_cd':'005930','result_list':[]},None),
                             (payload(),{'api-id':'ka11081'})):
            report = inspect(body,headers=headers)
            self.assertEqual(report['status'],'parser_error')
            self.assertFalse(report['b1_parse_success'])
        self.assertEqual(inspect([])['status'],'api_error')

    def test_response_headers_unknowns_and_credentials_are_removed_everywhere(self):
        body = payload()
        body.update(token=TOKEN,appkey=APP,account_no='1234567890',return_msg=f'Bearer {TOKEN} secretkey={SECRET}')
        body[daily.LIST_KEYS['ka10081']][0].update(authorization='Bearer '+TOKEN,unexpected={'token':TOKEN},
                                              upd_rt=SECRET)
        report = inspect(body,headers={'Authorization':TOKEN,'Set-Cookie':SECRET,'api-id':'ka10081','cont-yn':'Y','next-key':TOKEN})
        encoded = json.dumps(report,ensure_ascii=False)
        for value in (TOKEN,APP,SECRET,'1234567890','Bearer '+TOKEN): self.assertNotIn(value,encoded)
        self.assertNotIn('authorization',report['response_projection'])
        self.assertNotIn('Set-Cookie',report['response_headers'])
        self.assertFalse(report['continuation_followed'])
        self.assertEqual(report['pagination'],'more_available')

    def test_redaction_cannot_hide_invalid_original_continuation_length(self):
        report=inspect(headers={'cont-yn':'Y','next-key':'X'*100})
        self.assertNotIn('X'*100,json.dumps(report))
        self.assertFalse(report['b1_validated'])
        self.assertIn('invalid_next_key',report['page_issues'])

    def test_invalid_container_and_nonfinite_never_persist_nested_tokens(self):
        for value in ({'token':TOKEN},float('nan'),float('inf')):
            body=payload();body[daily.LIST_KEYS['ka10081']][0]['trde_prica']=value
            report=inspect(body)
            self.assertFalse(report['b1_validated'])
            self.assertNotIn(TOKEN,json.dumps(report,allow_nan=False))

    def test_adapter_is_reused_with_explicit_context_without_manufactured_evidence(self):
        request=req()
        page=daily.parse_response(payload(),request=request,response_environment='mock',request_started_at=NOW,
            response_received_at=NOW+timedelta(seconds=1),source='SYNTHETIC fixture',cont_yn='N')
        with patch.object(probe.adapter,'to_tracking',wraps=probe.adapter.to_tracking) as convert:
            result=probe.review_tracking(page,snapshot=snapshot(),calendar=calendar(),as_of=NOW+timedelta(seconds=2))
            self.assertEqual(result['observations_generated'],0)
            self.assertTrue(all('closing_evidence_missing' in r['reasons'] for r in result['holds']))
            self.assertEqual(convert.call_args.kwargs['evidence'],())
            convert.assert_called_once()

    def test_adapter_cannot_backdate_receipt_or_make_unknown_unit_proofs(self):
        page=daily.parse_response(payload(),request=req(),response_environment='mock',request_started_at=NOW,
            response_received_at=NOW+timedelta(seconds=1),source='SYNTHETIC fixture',cont_yn='N')
        result=probe.review_tracking(page,snapshot=snapshot(),calendar=calendar(),as_of=NOW)
        self.assertEqual(result['observations_generated'],0)
        self.assertTrue(all('response_not_yet_available' in row['reasons'] for row in result['holds']))
        self.assertTrue(all('closing_evidence_missing' in row['reasons'] for row in result['holds']))

    def test_receipt_time_reversal_is_not_accepted(self):
        report=probe.inspect_response(payload(),request=req(),started_at=NOW,received_at=NOW-timedelta(seconds=1),
                                      http_status=200,headers={})
        self.assertEqual(report['status'],'parser_error')


class DiagnosticTransportTests(unittest.TestCase):
    def setUp(self):
        self.session=Mock()
        self.patcher=patch('kiwoom_collector.client.requests.Session',return_value=self.session)
        self.patcher.start();self.addCleanup(self.patcher.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)

    def client(self,plans=None,**kwargs):
        client=probe.DailyDiagnosticClient(Credentials(APP,SECRET,kwargs.pop('environment','mock')),
            plans or (req(),),clock=lambda:NOW,real=kwargs.pop('real',True),**kwargs)
        self.addCleanup(client.close)
        return client

    def test_explicit_real_gate_at_client_layer(self):
        with self.assertRaises(ValueError): self.client(real=False)
        self.session.post.assert_not_called()

    def test_default_auth_is_blocked_no_oauth_request(self):
        client=self.client()
        self.assertFalse(client.prepare_auth())
        self.session.post.assert_not_called()
        self.assertEqual(client.counts,{'oauth':0,'daily':0})

    def test_supplied_token_single_post_uses_existing_pacing_and_no_continuation(self):
        self.session.post.return_value=response(headers={'cont-yn':'Y','next-key':'more'})
        client=self.client(access_token=TOKEN)
        self.assertTrue(client.prepare_auth())
        report=client.probe(req())
        self.assertEqual(client.counts,{'oauth':0,'daily':1})
        self.assertEqual(report['pagination'],'more_available')
        self.assertEqual(self.session.post.call_count,1)
        args=self.session.post.call_args
        self.assertEqual(args.args[0],'https://mockapi.kiwoom.com/api/dostk/chart')
        self.assertEqual(args.kwargs['json'],req().body)
        self.assertEqual(args.kwargs['headers']['api-id'],'ka10081')
        self.assertFalse(args.kwargs['allow_redirects'])
        again=client.probe(req())
        self.assertEqual(again['status'],'request_blocked')
        self.assertIsNone(again['request_started_at'])
        self.assertEqual(self.session.post.call_count,1)

    def test_in_memory_valid_token_is_reused_without_auth(self):
        client=self.client()
        client.token=Token(TOKEN,NOW+timedelta(hours=1))
        self.assertTrue(client.prepare_auth())
        self.session.post.assert_not_called()
        self.assertEqual(client.auth_diagnostic['status'],'existing_in_memory_token')

    def test_explicit_oauth_only_once_and_never_saved_in_diagnostics(self):
        self.session.post.side_effect=[response({'token':TOKEN,'token_type':'bearer','expires_dt':'20990101000000','return_code':0}),response()]
        client=self.client(allow_auth=True)
        self.assertTrue(client.prepare_auth())
        self.assertEqual(client.probe(req())['status'],'parsed')
        self.assertEqual(client.counts,{'oauth':1,'daily':1})
        self.assertNotIn(TOKEN,json.dumps(client.auth_diagnostic))
        self.assertTrue(client.prepare_auth())
        self.assertEqual(client.counts['oauth'],1)

    def test_oauth_failure_reports_actual_code_and_no_chart(self):
        self.session.post.return_value=response({'return_code':8001,'return_msg':'키 검증 실패'},status=401)
        client=self.client(allow_auth=True)
        self.assertFalse(client.prepare_auth())
        self.assertEqual(client.auth_diagnostic['return_code'],8001)
        self.assertEqual(self.session.post.call_count,1)
        self.assertEqual(client.counts['daily'],0)

    def test_no_retry_on_rate_limit_token_expiry_or_transport_failure(self):
        for failure in (response({'return_code':1700,'return_msg':'호출 제한'}),
                        response({'return_code':8005,'return_msg':'Token invalid'}),requests.Timeout(TOKEN)):
            self.session.reset_mock();self.session.post.side_effect=None
            if isinstance(failure,Exception): self.session.post.side_effect=failure
            else: self.session.post.return_value=failure
            client=self.client(access_token=TOKEN,allow_auth=True)
            self.assertTrue(client.prepare_auth())
            report=client.probe(req())
            self.assertIn(report['status'],('api_error','transport_error'))
            self.assertEqual(client.counts,{'oauth':0,'daily':1})
            self.assertEqual(self.session.post.call_count,1)
            self.assertNotIn(TOKEN,json.dumps(report))

    def test_wrong_environment_future_dates_and_unapproved_code_blocked(self):
        with self.assertRaises(ValueError): self.client(plans=(req(environment='real'),))
        with self.assertRaises(ValueError): self.client(plans=(req(base_dt='20261030'),))
        client=self.client(access_token=TOKEN);client.prepare_auth()
        self.assertEqual(client.probe(req('000660'))['status'],'request_blocked')
        self.session.post.assert_not_called()

    def test_budget_guard_disallows_wrong_endpoint_and_replays_even_private_transport(self):
        client=self.client(access_token=TOKEN)
        with self.assertRaises(KiwoomError): client.session.post('https://api.kiwoom.com/api/dostk/chart',json=req().body,headers={'api-id':'ka10081'},allow_redirects=False)
        with self.assertRaises(KiwoomError): client.session.post('https://mockapi.kiwoom.com/oauth2/token',json={},headers={},allow_redirects=False)
        self.session.post.assert_not_called()

    def test_fail_fast_marks_second_stock_not_attempted_and_separate_json_not_sqlite(self):
        self.session.post.return_value=response({'return_code':8104,'return_msg':'모의 미지원'})
        client=self.client(plans=(req(),req('000660')),access_token=TOKEN);folder=Path(self.temp.name)
        report,path=probe.execute((req(),req('000660')),client,folder)
        self.assertEqual(report['results'][1]['status'],'not_attempted')
        self.assertEqual(self.session.post.call_count,1)
        self.assertEqual(path.name,'report.json')
        self.assertNotIn(TOKEN,path.read_text())
        self.assertFalse(list(folder.glob('*.db')))
        self.assertEqual(report['data_origin'],'mock_server_unverified_source')
        with self.assertRaises(FileExistsError): probe.save_report(folder,report)

    def test_two_stocks_one_page_each_and_environment_real_not_synthetic(self):
        plans=(req(environment='real'),req('000660',environment='real'))
        self.session.post.side_effect=[response(payload(r)) for r in plans]
        client=self.client(plans=plans,environment='real',access_token=TOKEN)
        client.request_interval=1.1
        with patch('kiwoom_collector.client.time.perf_counter',side_effect=(0,2,2,4)):
            report,_=probe.execute(plans,client,Path(self.temp.name))
        self.assertEqual(report['status'],'completed')
        self.assertEqual(client.counts,{'oauth':0,'daily':2})
        self.assertEqual(report['environment'],'real')
        self.assertNotEqual(report['data_origin'],'mock_server_unverified_source')


class DiagnosticCLITests(unittest.TestCase):
    def test_default_and_planned_requests_do_not_load_credentials_or_create_session(self):
        for args in ([],['--base-date','20261027','--api-id','ka11081'],['--environment','real']):
            with patch.object(probe,'load_credentials',side_effect=AssertionError('no secrets')),patch.object(probe,'DailyDiagnosticClient',side_effect=AssertionError('no HTTP client')),patch('sys.stdout',new_callable=io.StringIO) as out:
                self.assertEqual(probe.main(args),0)
                result=json.loads(out.getvalue())
                self.assertEqual(result['mode'],'plan_no_http')
                self.assertFalse(result['files_written'])
                self.assertFalse(result['credentials_loaded'])

    def test_real_without_historical_date_blocks_before_credentials(self):
        for date_args in ([],['--base-date','29990101'],['--base-date',probe.now().strftime('%Y%m%d')]):
            with patch.object(probe,'load_credentials',side_effect=AssertionError('blocked first')),patch('sys.stdout',new_callable=io.StringIO):
                self.assertEqual(probe.main(['--real',*date_args]),1)

    def test_duplicates_more_than_two_bad_codes_and_auth_without_real_are_rejected(self):
        cases=[['--stock-code','005930','--stock-code','005930'],['--stock-code','005930','--stock-code','000660','--stock-code','035420'],['--authenticate']]
        for args in cases:
            with patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit) as exc: probe.main(args)
            self.assertEqual(exc.exception.code,2)
        with patch('sys.stdout',new_callable=io.StringIO):
            self.assertEqual(probe.main(['--stock-code','../.env']),1)
            self.assertEqual(probe.main(['--stock-code','005930_NX']),1)

    def test_output_must_not_be_project_data_or_source_path(self):
        for path in (probe.PROJECT_ROOT/'data',probe.PROJECT_ROOT/'data'/'daily',probe.PROJECT_ROOT/'kiwoom_collector'):
            with self.assertRaises(ValueError): probe.output_root(path)
        self.assertEqual(probe.output_root(probe.DEFAULT_OUTPUT),probe.DEFAULT_OUTPUT.resolve())

    def test_real_cli_with_mocked_transport_uses_only_new_output_folder(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(probe,'load_credentials',return_value=Credentials(APP,SECRET)),patch.dict(os.environ,{'KIWOOM_ACCESS_TOKEN':TOKEN}),patch('kiwoom_collector.client.requests.Session') as session,patch('sys.stdout',new_callable=io.StringIO) as out:
            session.return_value.post.return_value=response(payload(req(base_dt='20261027'),rows=FIXTURE['rows'][1:]))
            self.assertEqual(probe.main(['--real','--base-date','20261027','--stock-code','005930','--output-dir',folder]),0)
            result=json.loads(out.getvalue())
            self.assertEqual(result['request_counts'],{'oauth':0,'daily':1})
            self.assertEqual(len(list(Path(folder).rglob('report.json'))),1)
            self.assertNotIn(TOKEN,out.getvalue())
            session.return_value.close.assert_called_once()

    def test_real_module_default_is_network_and_database_free(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'sitecustomize.py').write_text('''import socket,sqlite3

def blocked(*args,**kwargs): raise RuntimeError("NETWORK_OR_DATABASE_FORBIDDEN")
socket.socket.connect=blocked
socket.create_connection=blocked
sqlite3.connect=blocked
''',encoding='utf-8')
            env=os.environ.copy();env['PYTHONPATH']=str(root)+os.pathsep+str(probe.PROJECT_ROOT);env['PYTHONIOENCODING']='utf-8'
            for key in ('KIWOOM_APP_KEY','KIWOOM_SECRET_KEY','KIWOOM_ACCESS_TOKEN'): env.pop(key,None)
            result=subprocess.run([sys.executable,'-m','kiwoom_collector.daily_diagnostic'],cwd=root,env=env,capture_output=True,text=True,encoding='utf-8',timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['mode'],'plan_no_http')
            self.assertNotIn('NETWORK_OR_DATABASE_FORBIDDEN',result.stdout+result.stderr)
            self.assertTrue({p.name for p in root.iterdir()} <= {'sitecustomize.py','__pycache__'})


if __name__=='__main__': unittest.main()
