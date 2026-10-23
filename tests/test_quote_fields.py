"""Real-host diagnostics with fake HTTP and fresh temporary databases only."""
from contextlib import closing, redirect_stdout, redirect_stderr
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

import requests

from clock_helpers import request_clock
from kiwoom_collector.client import KST
from kiwoom_collector.config import Credentials
from kiwoom_collector.quote_fields import fields_test, observation, FieldClient, DiagnosticStop
from kiwoom_collector.__main__ import main


def reply(data, status=200, headers=None):
    return Mock(status_code=status, headers=headers or {}, json=Mock(return_value=data))


def market(code, api):
    down = code == '000660'
    values = dict(stk_cd=code, cur_prc='-099000' if down else '+101000',
                  flu_rt='-1.00' if down else '+1.00', trde_qty='1000', trde_prica='100.50',
                  open_pric='+100000', high_pric='+102000', low_pric='-98000')
    if api == 'ka10095':
        values.update(base_pric='100000', pred_pre='-1000' if down else '+1000',
                      pred_pre_sig='5' if down else '2', dt='20261023', cntr_tm='100000', bid_tm='100001')
    else:
        values.update(pred_close_pric='100000', smbol='5' if down else '2', date='20261023', tm='100002')
    return values


class FieldTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.path = self.folder / 'fields.db'
        self.codes = ['005930', '000660']
        self.credentials = Credentials('FAKE_APP_KEY_ONLY', 'FAKE_SECRET_KEY_ONLY', 'real')
        self.clock = self.enterContext(request_clock())
        self.session = self.enterContext(patch('kiwoom_collector.client.requests.Session')).return_value
        self.auth = dict(return_code=0, token='FAKE_TOKEN_ONLY', token_type='bearer', expires_dt='20990101000000')
        self.quote = lambda api, codes: reply(dict(return_code=0, atn_stk_infr=[market(c, api) for c in codes])
                                           if api == 'ka10095' else dict(return_code=0, **market(codes[0], api)))
        self.starts = []

        def dispatch(url, **kwargs):
            self.starts.append(self.clock.now())
            api = kwargs['headers'].get('api-id')
            if api is None:
                return reply(self.auth)
            return self.quote(api, kwargs['json']['stk_cd'].split('|'))

        self.dispatch = dispatch
        self.session.post.side_effect = dispatch

    def run_test(self, **options):
        return fields_test(self.path, self.credentials, self.codes, **options)

    def rows(self, sql):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(sql)]

    def test_four_calls_shared_token_no_ranking_and_independent_schema(self):
        report, code = self.run_test(phase='intraday')
        self.assertEqual((code, report['status']), (0, 'complete'))
        self.assertEqual(report['request_counts'], {'auth': 1, 'ka10095': 1, 'ka10007': 2})
        calls = self.session.post.call_args_list
        self.assertEqual([v.kwargs['headers'].get('api-id') for v in calls], [None, 'ka10095', 'ka10007', 'ka10007'])
        self.assertTrue(all(v.args[0].startswith('https://api.kiwoom.com/') for v in calls))
        self.assertEqual(calls[1].kwargs['json'], {'stk_cd': '005930|000660'})
        self.assertEqual([v.kwargs['json']['stk_cd'] for v in calls[2:]], self.codes)
        self.assertEqual({v.kwargs['headers']['authorization'] for v in calls[1:]}, {'Bearer FAKE_TOKEN_ONLY'})
        self.assertTrue(all(v.kwargs.get('verify') is not False for v in calls))
        self.assertTrue(all(v.kwargs['allow_redirects'] is False for v in calls))
        self.assertTrue(all(b-a >= 1.2-1e-9 for a,b in zip(self.starts,self.starts[1:])))
        self.assertEqual(report['observation_counts'], dict(success=4, partial=0, error=0))
        self.assertEqual(report['uncollected_observations'], 0)
        self.assertEqual({r['name'] for r in self.rows("SELECT name FROM sqlite_master WHERE type='table'")},
                         {'field_runs', 'field_requests', 'field_observations', 'field_comparisons'})
        self.assertEqual(self.rows('SELECT environment,test_mode,phase FROM field_runs'),
                         [dict(environment='real', test_mode=1, phase='intraday')])
        self.assertEqual(len(self.rows('SELECT * FROM field_observations')), 4)
        self.assertEqual(self.rows('PRAGMA foreign_key_check'), [])
        self.assertEqual(self.rows('PRAGMA integrity_check'), [{'integrity_check': 'ok'}])
        self.session.close.assert_called_once()

    def test_raw_signs_units_and_reference_calculations_not_promoted_to_verified(self):
        report, _ = self.run_test()
        down = next(v for v in report['observations'] if v['code']=='000660' and v['api_id']=='ka10095')
        self.assertEqual(down['raw']['cur_prc'], '-099000')
        n = down['normalized']
        self.assertEqual(n['current_price_abs_for_comparison'], '99000')
        self.assertEqual(n['price_status'], 'unverified_sign_semantics')
        self.assertEqual(n['turnover_won'], '100500000.00')
        self.assertEqual(n['unit_status'], 'spec_declared_not_independently_verified')
        self.assertEqual((n['symbol_direction'], n['rate_direction'], n['price_magnitude_direction']), ('down','down','down'))
        self.assertEqual(n['symbol_vs_rate'], 'consistent')
        self.assertEqual(n['pred_pre_vs_reference'], 'consistent')
        self.assertEqual(n['api_time_kind'], 'execution_time')
        self.assertTrue(all(v['price_sign_semantics']=='unverified' for v in report['comparisons']))

    def test_different_api_values_are_sequential_observations_not_false_unit_proof(self):
        default = self.quote
        def quote(api, codes):
            result = default(api, codes)
            if api=='ka10007':
                data=result.json.return_value
                data.update(cur_prc='+102000', flu_rt='+2.00', trde_qty='1001')
            return result
        self.quote=quote
        report, code = self.run_test()
        self.assertEqual(code, 0)
        self.assertEqual(report['comparisons'][0]['fields']['current_price_abs_for_comparison']['comparison'], 'different_observations')
        self.assertEqual(report['comparisons'][0]['observation_timing'], 'sequential_not_atomic')

    def test_empty_zero_invalid_and_missing_fields_remain_distinct(self):
        row=market('005930','ka10007')
        row.update(cur_prc='', flu_rt='0.00', trde_qty='0', trde_prica='0', tm='256000')
        row.pop('pred_close_pric')
        value=observation(row,'005930','ka10007')
        self.assertEqual(value['status'],'partial')
        self.assertEqual(value['raw']['cur_prc'],'')
        self.assertIsNone(value['normalized']['current_price_abs_for_comparison'])
        self.assertEqual(value['normalized']['volume_shares'],0)
        self.assertEqual(value['normalized']['turnover_won'],'0')
        self.assertIsNone(value['normalized']['reference_price'])
        self.assertIn('invalid_format:tm',value['issues'])

    def test_numeric_direction_and_reference_contradictions_are_visible(self):
        row=market('005930','ka10095')
        row.update(cur_prc='-101000',flu_rt='-1.00',pred_pre_sig='2',base_pric='100000',pred_pre='-1000')
        n=observation(row,'005930','ka10095')['normalized']
        self.assertEqual(n['symbol_vs_rate'],'different')
        self.assertEqual(n['rate_vs_reference'],'different')
        self.assertEqual(n['pred_pre_vs_reference'],'different')
        self.assertEqual(n['price_status'],'unverified_sign_semantics')

    def test_zero_reference_negative_volume_amount_and_bad_numeric_are_unverified(self):
        row=market('005930','ka10095')
        row.update(base_pric='0',trde_qty='-1',trde_prica='-1',flu_rt='NaN',cur_prc={'secret':'x'})
        v=observation(row,'005930','ka10095')
        for key in ('current_price_abs_for_comparison','change_percent','volume_shares','turnover_won','calculated_rate_percent'):
            self.assertIsNone(v['normalized'][key])
        self.assertEqual(v['status'],'partial')

    def test_large_decimal_values_are_exact(self):
        row=market('005930','ka10007');row['trde_prica']='12345678901234567890.12'
        self.assertEqual(observation(row,'005930','ka10007')['normalized']['turnover_won'], '12345678901234567890120000.00')

    def test_unknown_server_text_headers_and_credentials_are_never_persisted(self):
        default=self.quote
        def quote(api,codes):
            result=default(api,codes);data=result.json.return_value
            data.update(return_msg='PRIVATE_SERVER_MESSAGE',token='PRIVATE_TOKEN',account='PRIVATE_ACCOUNT')
            rows=data['atn_stk_infr'] if api=='ka10095' else [data]
            for row in rows:
                row.update(stk_nm='PRIVATE_PERSON',cur_prc='FAKE_SECRET_KEY_ONLY',open_pric='PRIVATE_SERVER_MESSAGE')
            return result
        self.quote=quote
        report, code=self.run_test()
        self.assertEqual((code,report['status']),(1,'partial'))
        with closing(sqlite3.connect(self.path)) as db:
            text='\n'.join(db.iterdump())+json.dumps(report)
        for secret in ('FAKE_APP_KEY_ONLY','FAKE_SECRET_KEY_ONLY','FAKE_TOKEN_ONLY','PRIVATE_SERVER_MESSAGE','PRIVATE_TOKEN','PRIVATE_ACCOUNT','PRIVATE_PERSON'):
            self.assertNotIn(secret,text)

    def test_environment_mismatch_sends_zero_and_creates_nothing(self):
        for credentials in (Credentials('fake','fake','mock'),
                            SimpleNamespace(environment='real',base_url='https://example.invalid',app_key='fake',secret_key='fake')):
            with self.assertRaises(ValueError): fields_test(self.path,credentials,self.codes)
        self.assertFalse(self.path.exists())
        self.session.post.assert_not_called()

    def test_invalid_inputs_sends_zero_and_creates_nothing(self):
        for codes in ([], ['005930'], ['005930','005930'], ['bad','000660'], ['005930','000660','035420']):
            with self.assertRaises(ValueError): fields_test(self.path,self.credentials,codes)
        for options in (dict(spacing=1),dict(spacing=float('nan')),dict(budget=1),dict(budget=float('inf')),dict(phase='unknown'),dict(environment='mock')):
            with self.assertRaises(ValueError): self.run_test(**options)
        self.assertFalse(self.path.exists())
        self.session.post.assert_not_called()

    def test_existing_database_and_sidecars_never_open_or_change(self):
        self.path.write_bytes(b'original')
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        with patch('kiwoom_collector.quote_fields.sqlite3.connect', side_effect=AssertionError('no SQLite open')):
            with self.assertRaises(ValueError):self.run_test()
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)
        self.path.unlink()
        for suffix in ('-wal','-shm','-journal','.real.api.lock','.mock.api.lock'):
            side=Path(str(self.path)+suffix);side.write_bytes(b'original')
            with self.assertRaises(ValueError):self.run_test()
            self.assertEqual(side.read_bytes(),b'original');side.unlink()
        self.session.post.assert_not_called()

    def test_completed_run_cannot_be_replayed_or_overwritten(self):
        self.run_test();before=self.path.read_bytes();self.session.post.reset_mock()
        with self.assertRaises(ValueError):self.run_test()
        self.assertEqual(self.path.read_bytes(),before)
        self.session.post.assert_not_called()

    def test_http_auth_and_quote_failures_stop_without_retry(self):
        for stage in ('auth','ka10095','ka10007'):
            for status in (401,403,429,503):
                with self.subTest(stage=stage,status=status):
                    self.path=self.folder/f'{stage}-{status}.db';self.session.post.reset_mock()
                    def post(url,**kwargs):
                        api=kwargs['headers'].get('api-id','auth')
                        return reply({'return_msg':'PRIVATE'},status) if api==stage else self.dispatch(url,**kwargs)
                    self.session.post.side_effect=post
                    report,code=self.run_test()
                    self.assertEqual(code,1)
                    self.assertEqual(self.session.post.call_count,{'auth':1,'ka10095':2,'ka10007':3}[stage])
                    self.assertEqual(report['requests'][-1]['http_status'],status)
                    self.assertNotIn('PRIVATE',json.dumps(report))

    def test_api_rate_auth_and_unsupported_errors_stop_without_retry(self):
        for error_code in (1700,8005,8104,999):
            self.path=self.folder/f'api-{error_code}.db';self.session.post.reset_mock()
            self.quote=lambda *args:reply({'return_code':error_code,'return_msg':'PRIVATE'})
            report,code=self.run_test()
            self.assertEqual(code,1)
            self.assertEqual(report['http_attempt_count'],2)
            self.assertEqual(report['requests'][-1]['return_code'],error_code)

    def test_invalid_authentication_never_starts_quotes(self):
        for data in ({'return_code':0}, dict(self.auth,expires_dt='invalid'), dict(self.auth,expires_dt='20000101000000')):
            self.path=self.folder/(str(len(list(self.folder.iterdir())))+'.db');self.auth=data
            report,code=self.run_test()
            self.assertEqual(code,1)
            self.assertEqual(report['http_attempt_count'],1)

    def test_short_lived_token_is_not_reissued(self):
        from datetime import timedelta
        self.auth['expires_dt']=(datetime.now(KST)+timedelta(seconds=15)).strftime('%Y%m%d%H%M%S')
        report,code=self.run_test()
        self.assertEqual((code,report['reason']),(1,'token_expiring_no_refresh'))
        self.assertEqual(report['http_attempt_count'],1)

    def test_tls_failure_is_not_bypassed_or_retried(self):
        self.session.post.side_effect=requests.exceptions.SSLError('PRIVATE')
        report,code=self.run_test()
        self.assertEqual((code,report['reason']),(1,'tls_failed'))
        self.assertEqual(report['http_attempt_count'],1)
        self.assertEqual(report['requests'][0]['status'],'error')
        self.assertNotIn('PRIVATE',json.dumps(report))

    def test_transmission_unknown_preserves_completed_bulk_and_unsent_targets(self):
        self.quote=lambda api,codes: (_ for _ in ()).throw(requests.Timeout('PRIVATE')) if api=='ka10007' else reply({'return_code':0,'atn_stk_infr':[market(c,api) for c in codes]})
        report,code=self.run_test()
        self.assertEqual((code,report['status'],report['reason']),(1,'uncertain','transmission_unknown'))
        self.assertEqual((report['http_attempt_count'],len(report['observations']),report['uncollected_observations']),(3,2,2))
        self.assertEqual(report['requests'][-1]['status'],'uncertain')

    def test_interrupt_preserves_uncertain_request(self):
        self.quote=lambda *args: (_ for _ in ()).throw(KeyboardInterrupt())
        report,code=self.run_test()
        self.assertEqual((code,report['status']),(130,'uncertain'))
        self.assertEqual(report['http_attempt_count'],2)
        self.assertEqual(report['requests'][-1]['status'],'uncertain')

    def test_budget_between_requests_does_not_invent_unsent_rows(self):
        def post(url,**kwargs):
            result=self.dispatch(url,**kwargs)
            if url.endswith('/oauth2/token'):self.clock.seconds+=91
            return result
        self.session.post.side_effect=post
        report,code=self.run_test()
        self.assertEqual((code,report['reason']),(1,'time_budget'))
        self.assertEqual((report['http_attempt_count'],len(report['requests'])),(1,1))

    def test_budget_rechecked_after_pacing_stores_not_sent(self):
        report,code=self.run_test(budget=60,spacing=35)
        self.assertEqual((code,report['reason']),(1,'time_budget'))
        self.assertEqual(report['http_attempt_count'],1)
        self.assertEqual(report['requests'][-1]['status'],'not_sent')
        self.assertIsNone(report['requests'][-1]['started_at'])

    def test_missing_duplicate_unexpected_codes_and_continuation_stop(self):
        cases=[reply({'return_code':0,'atn_stk_infr':[]}),
               reply({'return_code':0,'atn_stk_infr':[market('005930','ka10095')]*2}),
               reply({'return_code':0,'atn_stk_infr':[market('035420','ka10095'),market('000660','ka10095')]}),
               reply({'return_code':0,'atn_stk_infr':[]},headers={'cont-yn':'Y'}),reply({'return_code':0}),reply([])]
        for index,value in enumerate(cases):
            self.path=self.folder/f'invalid-{index}.db';self.quote=lambda *args:value
            report,code=self.run_test()
            self.assertEqual((code,report['http_attempt_count']),(1,2))
            self.assertEqual(report['observations'],[])

    def test_partial_fields_do_not_fabricate_values_or_block_other_api_evidence(self):
        original=self.quote
        def quote(api,codes):
            result=original(api,codes)
            if api=='ka10095':result.json.return_value['atn_stk_infr'][0].pop('trde_prica')
            return result
        self.quote=quote
        report,code=self.run_test()
        self.assertEqual((code,report['status'],report['http_attempt_count']),(1,'partial',4))
        self.assertEqual(report['comparisons'][0]['fields']['turnover_won']['comparison'],'unverified')

    def test_storage_failure_preserves_prior_observations_and_stops(self):
        actual_connect=sqlite3.connect
        class Failing(sqlite3.Connection):
            def execute(connection,sql,parameters=()):
                if sql.startswith('INSERT INTO field_observations') and parameters[2]=='ka10007':
                    raise sqlite3.OperationalError('PRIVATE')
                return super(Failing,connection).execute(sql,parameters)
        with patch('kiwoom_collector.quote_fields.sqlite3.connect',side_effect=lambda *a,**kw:actual_connect(*a,**kw,factory=Failing)):
            report,code=self.run_test()
        self.assertEqual((code,report['reason'],report['http_attempt_count']),(1,'storage_failed',3))
        self.assertEqual(len(report['observations']),2)
        self.assertEqual(report['requests'][-1]['status'],'uncertain')
        self.assertNotIn('PRIVATE',json.dumps(report))

    def test_final_storage_failure_does_not_report_zero_as_verified_counts(self):
        actual_connect=sqlite3.connect
        class Failing(sqlite3.Connection):
            def execute(connection,sql,parameters=()):
                if sql.startswith('INSERT INTO field_comparisons'):
                    raise sqlite3.OperationalError('PRIVATE')
                return super(Failing,connection).execute(sql,parameters)
        with patch('kiwoom_collector.quote_fields.sqlite3.connect',side_effect=lambda *a,**kw:actual_connect(*a,**kw,factory=Failing)):
            report,code=self.run_test()
        self.assertEqual((code,report['status'],report['http_attempt_count']),(1,'uncertain',4))
        self.assertFalse(report['counts_verified'])
        self.assertEqual(report['observation_counts'],dict(success=None,partial=None,error=None))
        self.assertIsNone(report['uncollected_observations'])
        self.assertEqual(len(self.rows('SELECT * FROM field_observations')),4)
        self.assertEqual(self.rows('SELECT status FROM field_runs'),[{'status':'running'}])

    def test_storage_failure_before_send_never_sends_that_request(self):
        actual_connect=sqlite3.connect
        starts=[]
        class Failing(sqlite3.Connection):
            def execute(connection,sql,parameters=()):
                if sql.startswith('UPDATE field_requests SET started_at='):
                    starts.append(1)
                    if len(starts)==3:
                        raise sqlite3.OperationalError('PRIVATE')
                return super(Failing,connection).execute(sql,parameters)
        with patch('kiwoom_collector.quote_fields.sqlite3.connect',side_effect=lambda *a,**kw:actual_connect(*a,**kw,factory=Failing)):
            report,code=self.run_test()
        self.assertEqual((code,report['http_attempt_count']),(1,2))
        self.assertEqual(report['requests'][-1]['status'],'not_sent')
        self.assertEqual(len(report['observations']),2)

    def test_last_response_over_budget_is_preserved_but_not_reported_complete(self):
        count=[]
        def post(url,**kwargs):
            result=self.dispatch(url,**kwargs);count.append(1)
            if len(count)==4:self.clock.seconds+=121
            return result
        self.session.post.side_effect=post
        report,code=self.run_test()
        self.assertEqual((code,report['reason']),(1,'time_budget_after_response'))
        self.assertEqual((report['http_attempt_count'],len(report['observations'])),(4,4))

    def test_runtime_environment_change_stops_before_next_http(self):
        self.credentials=SimpleNamespace(environment='real',base_url='https://api.kiwoom.com',app_key='fake-key',secret_key='fake-secret')
        def post(url,**kwargs):
            result=self.dispatch(url,**kwargs)
            if url.endswith('/oauth2/token'):self.credentials.environment='mock'
            return result
        self.session.post.side_effect=post
        report,code=self.run_test()
        self.assertEqual((code,report['reason'],report['http_attempt_count']),(1,'environment_mismatch',1))

    def test_unexpected_transport_exception_does_not_leak_or_replay(self):
        self.quote=lambda *args: (_ for _ in ()).throw(RuntimeError('PRIVATE'))
        report,code=self.run_test()
        self.assertEqual((code,report['status'],report['http_attempt_count']),(1,'uncertain',2))
        self.assertNotIn('PRIVATE',json.dumps(report))

    def test_invalid_json_missing_and_boolean_return_codes_stop(self):
        bad_json=reply({});bad_json.json.side_effect=ValueError('PRIVATE')
        cases=[bad_json,reply({}),reply({'return_code':True}),reply({'return_code':'PRIVATE'})]
        for index,value in enumerate(cases):
            self.path=self.folder/f'code-{index}.db';self.quote=lambda *args:value
            report,code=self.run_test()
            self.assertEqual((code,report['http_attempt_count']),(1,2))
            self.assertNotIn('PRIVATE',json.dumps(report))

    def test_guard_rejects_orders_rank_mock_and_auth_replay(self):
        client=FieldClient(self.credentials,clock=self.clock.now,now=lambda:datetime.now(KST),budget=120,on_start=lambda _:None)
        self.addCleanup(client.close)
        for path,headers in (('/api/dostk/ordr',{'api-id':'kt10000'}),('/api/dostk/stkinfo',{'api-id':'ka00198'})):
            with self.assertRaises(DiagnosticStop):client._request(path,{},headers)
        client.counts['auth']=1
        with self.assertRaises(DiagnosticStop):client.authenticate()
        self.session.post.assert_not_called()

    def test_cli_summary_is_safe_json_plus_console_and_no_store_or_scheduler(self):
        stdout,stderr=io.StringIO(),io.StringIO()
        with patch('kiwoom_collector.__main__.load_credentials',return_value=self.credentials), \
             patch('kiwoom_collector.__main__.Store',side_effect=AssertionError('no Store')), \
             patch('kiwoom_collector.__main__.collect_schedule',side_effect=AssertionError('no schedule')), \
             redirect_stdout(stdout),redirect_stderr(stderr):
            code=main(['quote-fields-test','--environment','real','--codes',*self.codes,'--db',str(self.path),'--phase','intraday'])
        report=json.loads(stdout.getvalue())
        self.assertEqual((code,report['http_attempt_count']),(0,4))
        self.assertIn('HTTP 4/4',stderr.getvalue())
        for secret in ('FAKE_TOKEN_ONLY','FAKE_APP_KEY_ONLY','FAKE_SECRET_KEY_ONLY'):
            self.assertNotIn(secret,stdout.getvalue()+stderr.getvalue())

    def test_cli_missing_or_misplaced_arguments_fail_before_credentials(self):
        base=['quote-fields-test','--environment','real','--codes',*self.codes,'--db',str(self.path)]
        variants=[['quote-fields-test'],base[:-2],['quote-fields-test','--environment','mock','--codes',*self.codes,'--db',str(self.path)],
                  base+['--collection-id','anything'],base+['--schedule'],base+['--qry-tp','1'],['once','--codes',*self.codes]]
        for argv in variants:
            with patch('sys.stderr'),patch('kiwoom_collector.__main__.load_credentials',side_effect=AssertionError('no credentials')),self.assertRaises(SystemExit):
                main(argv)
        self.assertFalse(self.path.exists())
        self.session.post.assert_not_called()

    def test_real_module_entrypoint_with_blocked_network_and_fake_http(self):
        guard=self.folder/'guard';guard.mkdir()
        (guard/'sitecustomize.py').write_text('''import socket,requests,dotenv
from unittest.mock import Mock
dotenv.load_dotenv=lambda *a,**kw:False
def forbidden(*a,**kw):raise RuntimeError('NETWORK_FORBIDDEN')
socket.socket.connect=forbidden
socket.getaddrinfo=forbidden
def post(url,**kw):
    api=kw['headers'].get('api-id')
    if api is None:
        data=dict(return_code=0,token='FAKE_TOKEN_ONLY',token_type='bearer',expires_dt='20990101000000')
    else:
        rows=[]
        for code in kw['json']['stk_cd'].split('|'):
            row=dict(stk_cd=code,cur_prc='+101',flu_rt='+1',trde_qty='100',trde_prica='1',open_pric='+100',high_pric='+102',low_pric='-99')
            if api=='ka10095':row.update(base_pric='100',pred_pre='+1',pred_pre_sig='2',dt='20261023',cntr_tm='100000')
            else:row.update(pred_close_pric='100',smbol='2',date='20261023',tm='100002')
            rows.append(row)
        data=dict(return_code=0,atn_stk_infr=rows) if api=='ka10095' else dict(return_code=0,**rows[0])
    return Mock(status_code=200,headers={},json=Mock(return_value=data))
requests.Session=lambda:Mock(post=Mock(side_effect=post))
''',encoding='utf-8')
        root=Path(__file__).resolve().parents[1]
        env=os.environ.copy()
        env.update(PYTHONPATH=os.pathsep.join((str(guard),str(root))),PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8',
                   KIWOOM_ENV='real',KIWOOM_APP_KEY='FAKE_APP_KEY_ONLY',KIWOOM_SECRET_KEY='FAKE_SECRET_KEY_ONLY')
        child=subprocess.run([sys.executable,'-B','-m','kiwoom_collector','quote-fields-test','--environment','real',
                              '--codes',*self.codes,'--db',str(self.path)],cwd=root,env=env,capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(child.returncode,0,child.stderr)
        report=json.loads(child.stdout)
        self.assertEqual((report['http_attempt_count'],len(report['observations'])),(4,4))
        self.assertIn('HTTP 4/4',child.stderr)
        for value in ('FAKE_TOKEN_ONLY','FAKE_APP_KEY_ONLY','FAKE_SECRET_KEY_ONLY','NETWORK_FORBIDDEN'):
            self.assertNotIn(value,child.stdout+child.stderr)

    def test_process_death_retains_running_and_reexecution_is_rejected(self):
        script='''import os,sys
from unittest.mock import Mock,patch
from kiwoom_collector.config import Credentials
from kiwoom_collector.quote_fields import fields_test
def post(*args,**kwargs): os._exit(39)
with patch('kiwoom_collector.client.requests.Session') as factory:
    factory.return_value.post.side_effect=post
    fields_test(sys.argv[1],Credentials('fake','fake','real'),['005930','000660'])
'''
        child=subprocess.run([sys.executable,'-B','-c',script,str(self.path)],capture_output=True,text=True,timeout=15)
        self.assertEqual(child.returncode,39,child.stderr)
        self.assertEqual(self.rows('SELECT status FROM field_runs'),[{'status':'running'}])
        self.assertEqual(self.rows('SELECT status FROM field_requests'),[{'status':'running'}])
        with self.assertRaises(ValueError):self.run_test()


if __name__ == '__main__':
    unittest.main()
