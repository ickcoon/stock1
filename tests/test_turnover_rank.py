"""Offline ka10032 normalization, isolated persistence, and mocked HTTP contract."""
from contextlib import closing, redirect_stdout
from copy import deepcopy
from datetime import datetime,timedelta
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

import requests
from clock_helpers import request_clock
from kiwoom_collector import turnover_rank as tr
from kiwoom_collector.client import KST,Token
from kiwoom_collector.config import Credentials
from kiwoom_collector.errors import KiwoomError


FIXTURE=Path(__file__).parent/'fixtures/ka10032_synthetic.json'


def fixture():return json.loads(FIXTURE.read_text(encoding='utf-8'))
def row():return deepcopy(fixture()['pages'][0]['body']['trde_prica_upper'][0])
def reply(data,status=200,headers=None):
    response=Mock(status_code=status,headers=headers or {})
    response.json.return_value=data
    return response


class TurnoverNormalizationTests(unittest.TestCase):
    def test_exact_raw_and_declared_units_no_empirical_verification(self):
        original=row();n=tr.normalize(original,tr.Profile())
        self.assertEqual(n['status'],'syntax_valid');self.assertEqual(n['issues'],[])
        self.assertEqual(n['raw'],original)
        v=n['normalized']
        self.assertEqual(v['turnover_won_diagnostic'],'1234560000')
        self.assertTrue(v['unit_spec_verified']);self.assertFalse(v['unit_runtime_verified'])
        self.assertIsNone(v['turnover_won_verified']);self.assertIsNone(v['current_price_won'])
        self.assertIsNone(v['server_asof']);self.assertIsNone(v['trade_date'])
        self.assertEqual(v['signed_price_diagnostic'],'70000')
        self.assertEqual((v['change_percent'],v['volume_shares']),('1.45','10000'))
        self.assertFalse(v['candidate_confirmed'])

    def test_amount_sign_invalid_strings_and_types_never_become_zero(self):
        for value in ('-1','-0','NaN','Infinity','1e6','1,000','１２３','',None,1,True):
            with self.subTest(value=value):
                r=row();r['trde_prica']=value;n=tr.normalize(r,tr.Profile())
                self.assertNotEqual(n['status'],'syntax_valid')
                self.assertIsNone(n['normalized']['turnover_won_diagnostic'])

    def test_zero_plus_decimal_and_large_amount_are_exact(self):
        for raw,expected in [('0','0'),('+0.000','0'),(' 1.25 ','1250000'),('99999999999999999999','99999999999999999999000000')]:
            r=row();r['trde_prica']=raw;n=tr.normalize(r,tr.Profile())
            self.assertEqual(n['normalized']['turnover_won_diagnostic'],expected)
            self.assertEqual(n['raw']['trde_prica'],raw)

    def test_price_sign_is_preserved_and_rates_are_signed(self):
        for value in ('+070000','-70000','0'):
            r=row();r['cur_prc']=value;r['flu_rt']='-0.55'
            n=tr.normalize(r,tr.Profile())
            self.assertEqual(n['raw']['cur_prc'],value)
            self.assertIsNone(n['normalized']['current_price_won'])
            self.assertEqual(n['normalized']['change_percent'],'-0.55')

    def test_rank_missing_zero_overflow_not_position_inferred(self):
        for value in ('0','-1','1.5','99999999999999999999',True,None):
            r=row();r['now_rank']=value;n=tr.normalize(r,tr.Profile())
            self.assertEqual(n['status'],'invalid');self.assertIsNone(n['normalized']['turnover_rank'])
        r=row();r.pop('now_rank');self.assertIsNone(tr.normalize(r,tr.Profile())['normalized']['turnover_rank'])
        r=row();r['pred_rank']='0';self.assertEqual(tr.normalize(r,tr.Profile())['normalized']['previous_day_rank'],'0')

    def test_volume_and_optional_fields_and_missing_metrics(self):
        for field,value in [('now_trde_qty','-1'),('now_trde_qty','1.2'),('flu_rt','2e1'),('sel_bid',False),('pred_pre_sig','9'),('stk_nm','  ')]:
            r=row();r[field]=value;self.assertTrue(tr.normalize(r,tr.Profile())['issues'])
        r=row();r.pop('flu_rt');self.assertIsNone(tr.normalize(r,tr.Profile())['normalized']['change_percent'])
        r=row();r.pop('pred_trde_qty');self.assertEqual(tr.normalize(r,tr.Profile())['status'],'syntax_valid')

    def test_code_zeros_suffixes_and_venue_no_automatic_merge(self):
        r=row();r['stk_cd']='000660';self.assertEqual(tr.normalize(r,tr.Profile())['normalized']['stock_code'],'000660')
        r['stk_cd']='12345A_NX';n=tr.normalize(r,tr.Profile(stex_tp='2'))
        self.assertEqual(n['normalized']['stock_code'],'12345A_NX')
        self.assertIn('code_venue_mismatch',tr.normalize(r,tr.Profile())['issues'])
        r['stk_cd']='A005930';self.assertEqual(tr.normalize(r,tr.Profile())['status'],'invalid')

    def test_profile_enums_and_no_extra_filters(self):
        for kw in ({'mrkt_tp':'0'},{'stex_tp':'KRX'},{'mang_stk_incls':0}):
            with self.assertRaises(ValueError):tr.Profile(**kw)
        self.assertEqual(tr.Profile().body(),dict(mrkt_tp='000',mang_stk_incls='0',stex_tp='1'))
        n=tr.normalize(row(),tr.Profile(mang_stk_incls='1'))['normalized']
        self.assertEqual(n['managed_stocks'],'included')
        self.assertIn('not_individually_filtered',n['other_security_types'])

    def test_invalid_row_and_unknown_fields_do_not_leak_or_mark_verified(self):
        self.assertEqual(tr.normalize([],tr.Profile())['status'],'invalid')
        r=row();r.update(token='do-not-save',authorization='private',unit_runtime_verified=True)
        n=tr.normalize(r,tr.Profile());self.assertNotIn('do-not-save',tr.dump(n))
        self.assertFalse(n['normalized']['unit_runtime_verified'])


class TurnoverCollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'turnover.db';self.profile=tr.Profile()

    def collect(self,doc=None,**kw):
        return tr.collect(self.path,tr.FixtureTransport(doc or fixture(),self.profile,'mock'),self.profile,**kw)

    def read(self,sql):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute(sql).fetchall()

    def two_pages(self):
        d=fixture();p=d['pages'][0];p['body']['trde_prica_upper']=p['body']['trde_prica_upper'][:1]
        p['headers']={'cont-yn':'Y','next-key':'opaque-page-key'}
        q=deepcopy(p);q['body']['trde_prica_upper'][0].update(stk_cd='000660',now_rank='2')
        q['headers']={'cont-yn':'N'};q['request_started_at']='2026-10-26T10:00:02+09:00';q['request_finished_at']='2026-10-26T10:00:03+09:00'
        d['pages'].append(q);return d

    def test_new_sqlite_and_integrity_and_distinct_tables(self):
        result=self.collect();self.assertEqual((result['status'],result['rows']),('success',2))
        info=tr.inspect_database(self.path)
        self.assertEqual(info['integrity_check'],['ok']);self.assertEqual(info['foreign_key_errors'],[])
        names={r[0] for r in self.read("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(names,{'turnover_runs','turnover_pages','turnover_rows'})
        self.assertEqual(self.read('SELECT source_kind,test_mode,environment FROM turnover_runs'),[('fixture',1,'mock')])
        self.assertEqual(self.read('SELECT stock_code,turnover_rank FROM turnover_rows ORDER BY row_no'),[('005930',1),('000660',2)])

    def test_existing_output_and_production_path_refused_before_transport(self):
        self.path.write_bytes(b'untouched');transport=Mock(source_kind='fixture')
        with self.assertRaises(ValueError):tr.collect(self.path,transport)
        self.assertEqual(self.path.read_bytes(),b'untouched');transport.fetch.assert_not_called()
        with patch.object(tr,'PROJECT_ROOT',Path(self.tmp.name)):
            with self.assertRaises(ValueError):tr.collect(Path(self.tmp.name)/'data/stock1.db',transport)
        with self.assertRaises(ValueError):tr.collect(self.path.with_suffix('.txt'),transport)

    def test_alias_to_existing_file_is_not_followed(self):
        self.path.write_bytes(b'untouched');alias=self.path.with_name('alias.db')
        try:alias.symlink_to(self.path)
        except OSError:return # Windows account may lack symbolic-link privileges.
        with self.assertRaises(ValueError):tr.collect(alias,Mock(source_kind='fixture'))
        self.assertEqual(self.path.read_bytes(),b'untouched')

    def test_successful_empty_is_distinct_not_success_data(self):
        d=fixture();d['pages'][0]['body']['trde_prica_upper']=[]
        result=self.collect(d);self.assertEqual((result['status'],result['rows']),('empty',0))

    def test_invalid_body_code_list_http_and_types_fail(self):
        cases=[dict(body=[]),dict(body={}),dict(body={'return_code':True}),dict(body={'return_code':0.0}),
               dict(body={'return_code':10**50}),dict(body={'return_code':0}),
               dict(body={'return_code':0,'trde_prica_upper':{}}),dict(http_status=429),dict(http_status=401),dict(http_status=503),
               dict(body={'return_code':1700}),dict(body={'return_code':8104})]
        for index,change in enumerate(cases):
            with self.subTest(index=index):
                self.path=Path(self.tmp.name)/('invalid-'+str(index)+'.db')
                d=fixture();d['pages'][0].update(change)
                result=self.collect(d);self.assertEqual(result['status'],'error');self.assertEqual(result['rows'],0)

    def test_missing_row_fields_partial_not_normal(self):
        d=fixture();d['pages'][0]['body']['trde_prica_upper'][0].pop('trde_prica')
        result=self.collect(d);self.assertEqual(result['status'],'partial')
        self.assertEqual(self.read('SELECT validation_status FROM turnover_rows ORDER BY row_no')[0],('partial',))

    def test_two_pages_keep_times_keys_and_actual_ranks(self):
        d=self.two_pages();transport=tr.FixtureTransport(d,self.profile,'mock')
        with patch.object(transport,'fetch',wraps=transport.fetch) as fetch:
            result=tr.collect(self.path,transport,max_pages=2)
        self.assertEqual(result['status'],'success');self.assertTrue(result['pagination_complete'])
        self.assertEqual(fetch.call_args_list[1].args[1],{'cont-yn':'Y','next-key':'opaque-page-key'})
        self.assertNotIn('opaque-page-key',str(self.read('SELECT * FROM turnover_pages')))
        self.assertEqual(self.read('SELECT request_started_at,request_finished_at FROM turnover_runs'),[('2026-10-26T10:00:00+09:00','2026-10-26T10:00:03+09:00')])

    def test_overlapping_or_cross_day_pages_are_not_success(self):
        for kind in ('overlap','day'):
            self.path=Path(self.tmp.name)/(kind+'.db');d=self.two_pages()
            d['pages'][1]['request_started_at']='2026-10-26T10:00:00+09:00' if kind=='overlap' else '2026-10-27T10:00:00+09:00'
            d['pages'][1]['request_finished_at']='2026-10-26T10:00:03+09:00' if kind=='overlap' else '2026-10-27T10:00:01+09:00'
            result=self.collect(d,max_pages=2)
            self.assertEqual(result['status'],'partial');self.assertFalse(result['pagination_complete'])

    def test_official_contract_fixture_fields_and_requested_profile(self):
        self.assertEqual(set(row()),set(tr.FIELDS))
        self.assertEqual(fixture()['api_id'],'ka10032')
        self.assertEqual(tr.API_PATH,'/api/dostk/rkinfo')
        self.assertEqual(set(tr.Profile().body()),{'mrkt_tp','mang_stk_incls','stex_tp'})
        self.assertIn('Synthetic',fixture()['description'])

    def test_page_limit_missing_and_repeated_keys_are_partial(self):
        for i,kind in enumerate(('limit','missing','repeat','empty')):
            self.path=Path(self.tmp.name)/(kind+'.db');d=self.two_pages()
            if kind=='missing':d['pages'][0]['headers']['next-key']=''
            if kind=='repeat':d['pages'][1]['headers']=deepcopy(d['pages'][0]['headers'])
            if kind=='empty':d['pages'][0]['body']['trde_prica_upper']=[]
            result=self.collect(d,max_pages=1 if kind=='limit' else 3)
            self.assertIn(result['status'],('partial','error'))
            self.assertFalse(result['pagination_complete'])

    def test_failed_second_page_keeps_first_without_success(self):
        d=self.two_pages();d['pages'][1]['http_status']=429
        result=self.collect(d,max_pages=2)
        self.assertEqual((result['status'],result['rows']),('partial',1))
        self.assertIn('rate_limited',result['issues'])

    def test_duplicate_codes_or_ranks_invalidate_all_colliding_rows(self):
        for field in ('stk_cd','now_rank'):
            self.path=Path(self.tmp.name)/(field+'.db');d=self.two_pages()
            d['pages'][1]['body']['trde_prica_upper'][0][field]=d['pages'][0]['body']['trde_prica_upper'][0][field]
            result=self.collect(d,max_pages=2)
            self.assertEqual(result['status'],'partial')
            self.assertEqual(self.read('SELECT validation_status FROM turnover_rows'),[('invalid',),('invalid',)])
            self.assertTrue(all('duplicate' in r[0] for r in self.read('SELECT issues_json FROM turnover_pages')))

    def test_missing_naive_or_reverse_time_not_synthesized(self):
        for i,(start,end) in enumerate([(None,None),('2026-10-26T10:00:00','2026-10-26T10:00:01'),('2026-10-26T10:00:02+09:00','2026-10-26T10:00:01+09:00')]):
            self.path=Path(self.tmp.name)/(str(i)+'.db');d=fixture()
            d['pages'][0].update(request_started_at=start,request_finished_at=end)
            result=self.collect(d);self.assertEqual(result['status'],'error');self.assertIn('invalid_request_times',result['issues'])

    def test_unknown_headers_messages_tokens_and_raw_errors_not_persisted(self):
        d=fixture();p=d['pages'][0];p['headers']['authorization']='Bearer must-not-save'
        p['body'].update(return_msg='must-not-save',token='must-not-save')
        p['body']['trde_prica_upper'][0]['token']='must-not-save'
        self.collect(d)
        self.assertNotIn('must-not-save',str(self.read('SELECT * FROM turnover_pages'))+str(self.read('SELECT * FROM turnover_rows')))

    def test_transport_exception_and_interruption_have_safe_status(self):
        for i,error in enumerate((RuntimeError('must-not-save'),KeyboardInterrupt())):
            self.path=Path(self.tmp.name)/(str(i)+'.db');t=Mock(source_kind='fixture');t.fetch.side_effect=error
            result=tr.collect(self.path,t)
            self.assertEqual(result['status'],'error' if i==0 else 'interrupted')
            self.assertNotIn('must-not-save',str(self.read('SELECT * FROM turnover_runs')))

    def test_fixture_requires_explicit_source_environment_and_request(self):
        for field,value in [('api_id','ka00198'),('environment','real'),('request',{}),('pages',[])]:
            d=fixture();d[field]=value
            with self.assertRaises(ValueError):tr.FixtureTransport(d,self.profile,'mock')

    def test_cli_plan_and_offline_inspect_are_network_free(self):
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')),patch.object(tr,'load_credentials',side_effect=AssertionError('credentials forbidden')):
            with redirect_stdout(io.StringIO()) as output:self.assertEqual(tr.main(['plan']),0)
            self.assertEqual(json.loads(output.getvalue())['network_calls'],0)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(tr.main(['offline','--db',str(self.path),'--fixture',str(FIXTURE)]),0)
                self.assertEqual(tr.main(['inspect','--db',str(self.path)]),0)

    def test_live_cli_requires_network_approval_before_credentials_or_database(self):
        with patch.object(tr,'load_credentials',side_effect=AssertionError('credentials forbidden')) as load:
            with redirect_stdout(io.StringIO()),self.assertRaises(SystemExit):tr.main(['live-test','--db',str(self.path)])
            load.assert_not_called();self.assertFalse(self.path.exists())

    def test_live_cli_environment_mismatch_never_constructs_transport(self):
        with patch.object(tr,'load_credentials',return_value=Credentials('key','secret','real')),patch.object(tr,'LiveTransport') as transport:
            with self.assertRaises(SystemExit):tr.main(['live-test','--db',str(self.path),'--network-approved'])
            transport.assert_not_called();self.assertFalse(self.path.exists())

    def test_cli_subprocess_and_empty_exit_code(self):
        env=dict(os.environ,PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1')
        result=subprocess.run([sys.executable,'-B','-m','kiwoom_collector.turnover_rank','offline','--fixture',str(FIXTURE),'--db',str(self.path)],env=env,capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout)['source_kind'],'fixture')
        d=fixture();d['pages'][0]['body']['trde_prica_upper']=[]
        fp=self.path.with_name('empty.json');fp.write_text(json.dumps(d),encoding='utf-8')
        with redirect_stdout(io.StringIO()):self.assertEqual(tr.main(['offline','--fixture',str(fp),'--db',str(self.path.with_name('empty.db'))]),3)


class TurnoverLiveContractTests(unittest.TestCase):
    def setUp(self):
        self.session=self.enterContext(patch('kiwoom_collector.client.requests.Session')).return_value
        self.enterContext(request_clock())
        self.client=tr.LiveTransport(Credentials('test-key','test-secret'))
        self.client.token=Token('test-only-token',datetime.now(KST)+timedelta(hours=1))
        self.addCleanup(self.client.close)

    def test_exact_endpoint_body_headers_and_no_retries(self):
        self.session.post.return_value=reply(fixture()['pages'][0]['body'])
        capture=self.client.fetch(tr.Profile(),{})
        call=self.session.post.call_args
        self.assertEqual(call.args,('https://mockapi.kiwoom.com/api/dostk/rkinfo',))
        self.assertEqual(call.kwargs['json'],tr.Profile().body())
        self.assertEqual(call.kwargs['headers']['api-id'],'ka10032')
        self.assertEqual(call.kwargs['headers']['authorization'],'Bearer test-only-token')
        self.assertFalse(call.kwargs['allow_redirects']);self.assertNotIn('verify',call.kwargs)
        self.assertIsNotNone(capture.started_at);self.assertIsNotNone(capture.finished_at)
        self.assertNotIn('test-only-token',repr(capture))

    def test_real_host_and_continuation_are_explicit(self):
        self.client.credentials=Credentials('test-real-key','test-real-secret','real')
        self.session.post.return_value=reply(fixture()['pages'][0]['body'])
        self.client.fetch(tr.Profile(stex_tp='3'),{'cont-yn':'Y','next-key':'cursor'})
        self.assertEqual(self.session.post.call_args.args[0],'https://api.kiwoom.com/api/dostk/rkinfo')
        self.assertEqual(self.session.post.call_args.kwargs['headers']['next-key'],'cursor')

    def test_http_api_json_network_failures_do_not_replay(self):
        for response in (reply({},429),reply({},401),reply({'return_code':1700}),reply([])):
            self.session.post.reset_mock();self.session.post.return_value=response
            capture=self.client.fetch(tr.Profile(),{})
            self.assertTrue(tr.analyze_capture(capture,tr.Profile())['issues']);self.assertEqual(self.session.post.call_count,1)
        self.session.post.side_effect=requests.Timeout('private error')
        capture=self.client.fetch(tr.Profile(),{});self.assertEqual(capture.error,'transport_failed')
        self.assertNotIn('private error',repr(capture))

    def test_auth_failure_is_not_stamped_as_quote_request(self):
        self.client.token=None
        with patch.object(self.client,'authenticate',side_effect=KiwoomError('private')) as auth:
            capture=self.client.fetch(tr.Profile(),{})
        auth.assert_called_once_with(attempts=1);self.session.post.assert_not_called()
        self.assertEqual(capture.error,'authentication_failed');self.assertIsNone(capture.started_at)

    def test_mid_pagination_token_expiry_stops_without_oauth_replay(self):
        self.client._ranking_started=True;self.client.token=None
        with patch.object(self.client,'authenticate') as auth:
            capture=self.client.fetch(tr.Profile(),{'cont-yn':'Y','next-key':'cursor'})
        auth.assert_not_called();self.assertEqual(capture.error,'authentication_failed')


if __name__=='__main__':unittest.main()
