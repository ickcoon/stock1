"""One-shot mock integration: fake HTTP, fresh temporary SQLite databases only."""
from contextlib import closing, redirect_stdout, redirect_stderr
from datetime import datetime, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
from clock_helpers import request_clock
from test_quotes import response
from test_batch_quotes import row, reply
from kiwoom_collector.client import KST
from kiwoom_collector.errors import KiwoomError
from kiwoom_collector.config import Credentials
from kiwoom_collector.integrated_test import integrated_test, fresh_database
from kiwoom_collector.integrated_test import IntegratedQuoteClient
from kiwoom_collector.api_resource import api_resource
from kiwoom_collector.__main__ import main


class IntegratedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.path=self.folder/'integrated.db'
        self.credentials=Credentials('fake-key','fake-secret')
        self.factory=patch('kiwoom_collector.client.requests.Session')
        self.session=self.factory.start().return_value
        self.addCleanup(self.factory.stop)
        self.request_clock=self.enterContext(request_clock())
        self.records=[dict(bigd_rank=str(i),stk_cd=f'{100000+i:06}',stk_nm=f'Stock{i}') for i in range(1,21)]
        self.auth=dict(return_code=0,token='DO_NOT_PRINT_TOKEN',token_type='bearer',expires_dt='20990101000000')
        self.ranking=lambda: response(dict(return_code=0,item_inq_rank=self.records))
        self.quotes=lambda codes: reply([row(c) for c in codes])
        self.http_starts=[]
        def dispatch(url,**kwargs):
            self.http_starts.append(self.request_clock.now())
            if url.endswith('/oauth2/token'): return response(self.auth)
            api=kwargs['headers']['api-id']
            if api=='ka00198': return self.ranking()
            return self.quotes(kwargs['json']['stk_cd'].split('|'))
        self.session.post.side_effect=dispatch

    def run_test(self,**kwargs):
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            return integrated_test(self.path,self.credentials,**kwargs)

    def rows(self,sql):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory=sqlite3.Row
            return [dict(r) for r in db.execute(sql)]

    def test_twenty_symbols_rank_once_quotes_ten_shared_token_and_modes(self):
        report,code=self.run_test()
        self.assertEqual((code,report['status'],report['success_count']),(0,'complete',20))
        self.assertEqual(report['request_counts'],dict(auth=1,ranking=1,quotes=10))
        self.assertEqual(self.session.post.call_count,12)
        self.assertTrue(all(b-a>=1.2-1e-9 for a,b in zip(self.http_starts,self.http_starts[1:])))
        parent=self.rows('SELECT * FROM collections')[0]
        self.assertEqual((parent['id'],parent['environment'],parent['test_mode'],parent['scheduled_at']),
                         (report['collection_id'],'mock',1,None))
        self.assertEqual({r['test_mode'] for r in self.rows('SELECT * FROM quote_batches')},{1})
        self.assertEqual({r['test_mode'] for r in self.rows('SELECT * FROM quote_observations')},{1})
        self.assertEqual({r['collection_id'] for r in self.rows('SELECT * FROM quote_observations')},{report['collection_id']})
        calls=self.session.post.call_args_list
        self.assertEqual({c.kwargs['headers']['authorization'] for c in calls[1:]},{'Bearer DO_NOT_PRINT_TOKEN'})
        self.assertGreaterEqual(report['elapsed_seconds'],0)
        self.assertEqual(report['uncollected_count'],0)
        self.assertEqual(self.rows('SELECT * FROM quote_top20_locks'),[])
        self.assertEqual(self.rows('PRAGMA foreign_key_check'),[])
        self.assertEqual(self.rows('PRAGMA integrity_check')[0]['integrity_check'],'ok')
        self.session.close.assert_called_once()

    def test_nineteen_symbols_ten_requests(self):
        self.records=self.records[:19]
        report,code=self.run_test()
        self.assertEqual((code,report['success_count'],report['request_counts']['quotes']),(0,19,10))
        self.assertEqual(self.session.post.call_args.kwargs['json']['stk_cd'],'100019')

    def test_explicit_minimum_spacing_keeps_all_twelve_request_starts_apart(self):
        report,code=self.run_test(spacing=1.1)
        self.assertEqual((code,report['http_attempt_count']),(0,12))
        self.assertTrue(all(b-a>=1.1-1e-9 for a,b in zip(self.http_starts,self.http_starts[1:])))

    def test_ranking_api_failure_no_retry_no_quote(self):
        self.ranking=lambda: response(dict(return_code=1700,return_msg='SENSITIVE_SERVER_MESSAGE'))
        report,code=self.run_test()
        self.assertEqual(code,1)
        self.assertEqual(report['request_counts'],dict(auth=1,ranking=1,quotes=0))
        self.assertEqual(self.rows('SELECT status FROM collections')[0]['status'],'error')

    def test_empty_ranking_stops(self):
        self.records=[]
        report,code=self.run_test()
        self.assertEqual((code,report['status'],report['target_count']),(1,'empty',0))
        self.assertEqual(report['request_counts']['quotes'],0)

    def test_ranking_continuation_does_not_make_second_request(self):
        self.ranking=lambda: response(dict(return_code=0,item_inq_rank=self.records),headers={'cont-yn':'Y','next-key':'fake'})
        report,code=self.run_test()
        self.assertEqual((code,report['request_counts']['ranking'],report['request_counts']['quotes']),(1,1,0))
        self.assertEqual(self.rows('SELECT * FROM ranking_records'),[])

    def test_environment_mismatch_rejected_before_db_or_http(self):
        with self.assertRaises(ValueError): integrated_test(self.path,Credentials('fake','fake','real'))
        self.assertFalse(self.path.exists()); self.session.post.assert_not_called()
        with self.assertRaises(ValueError): self.run_test(environment='real')
        self.assertFalse(self.path.exists()); self.session.post.assert_not_called()

    def test_existing_database_or_sidecars_never_opened(self):
        for suffix in ('','-wal','-shm','-journal','.mock.api.lock'):
            target=Path(str(self.path)+suffix)
            target.write_bytes(b'do not change')
            with self.assertRaises(ValueError): self.run_test()
            self.assertEqual(target.read_bytes(),b'do not change')
            target.unlink()
        self.session.post.assert_not_called()

    def test_default_operating_path_and_invalid_paths_rejected(self):
        from kiwoom_collector.storage import DEFAULT_DB
        for value in (None,DEFAULT_DB,self.folder/'bad.txt',self.folder/'missing'/'fresh.db'):
            with self.assertRaises(ValueError): fresh_database(value)
        self.session.post.assert_not_called()

    def test_duplicate_execution_rejected_without_extra_http(self):
        self.run_test()
        before=hashlib.sha256(self.path.read_bytes()).digest()
        with self.assertRaises(ValueError): self.run_test()
        self.assertEqual(self.session.post.call_count,12)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(),before)

    def test_atomic_path_claim_wins_before_store_and_http(self):
        original_open=Path.open
        def racing(path,*args,**kwargs):
            if path==self.path and args and args[0]=='xb':
                with original_open(path,'wb') as writer: writer.write(b'another owner')
            return original_open(path,*args,**kwargs)
        with patch.object(Path,'open',racing),self.assertRaises(FileExistsError): self.run_test()
        self.assertEqual(self.path.read_bytes(),b'another owner')
        self.session.post.assert_not_called()

    def test_partial_failures_are_counted_without_zero_substitution(self):
        self.quotes=lambda codes: reply([row(codes[0])])
        report,code=self.run_test()
        self.assertEqual((code,report['success_count'],report['failed_count'],report['uncollected_count']),(1,10,10,0))
        self.assertTrue(all(json.loads(r['normalized_json'])['change_percent'] is None for r in self.rows("SELECT * FROM quote_observations WHERE status='error'")))

    def test_partial_fields_have_separate_count(self):
        def partial(codes):
            rows=[row(c) for c in codes]
            for record in rows: record.pop('cur_prc')
            return reply(rows)
        self.quotes=partial
        report,code=self.run_test()
        self.assertEqual((code,report['partial_count'],report['success_count'],report['failed_count']),(1,20,0,0))

    def test_http_auth_and_rate_limits_stop_remaining_requests(self):
        for index,http in enumerate((401,403,429)):
            self.path=self.folder/f'http{index}.db'
            self.quotes=lambda codes: response(dict(return_code=0),status=http)
            report,code=self.run_test()
            self.assertEqual((code,report['request_counts']['quotes'],report['failed_count'],report['uncollected_count']),(1,1,2,18))

    def test_api_auth_rate_and_mock_unsupported_stop(self):
        for index,api_code in enumerate((8005,8031,1700,8104)):
            self.path=self.folder/f'api{index}.db'
            self.quotes=lambda codes: response(dict(return_code=api_code,return_msg='SENSITIVE_MESSAGE'))
            report,code=self.run_test()
            self.assertEqual((code,report['request_counts']['quotes'],report['uncollected_count']),(1,1,18))

    def test_initial_authentication_failure_not_retried(self):
        self.auth=dict(return_code=8005,return_msg='SENSITIVE_MESSAGE')
        report,code=self.run_test()
        self.assertEqual((code,report['request_counts']),(1,dict(auth=1,ranking=0,quotes=0)))

    def test_authentication_reissue_blocked(self):
        self.auth['expires_dt']=(datetime.now(KST)+timedelta(seconds=20)).strftime('%Y%m%d%H%M%S')
        report,code=self.run_test()
        self.assertEqual(code,1)
        self.assertEqual(report['reason'],'authentication_replay_blocked')
        self.assertEqual(report['request_counts'],dict(auth=1,ranking=1,quotes=0))

    def test_timeout_preserves_uncertain_request_and_lock(self):
        def timeout(codes): raise requests.Timeout('SENSITIVE_MESSAGE')
        self.quotes=timeout
        report,code=self.run_test()
        self.assertEqual((code,report['status'],report['uncertain_count'],report['uncollected_count']),(1,'uncertain',2,18))
        self.assertEqual(self.rows('SELECT status FROM quote_requests')[0]['status'],'uncertain')
        self.assertEqual(len(self.rows('SELECT * FROM quote_top20_locks')),1)

    def test_ctrl_c_preserves_previous_success_and_reports_unknown(self):
        calls=[0]
        def interrupt(codes):
            calls[0]+=1
            if calls[0]==2: raise KeyboardInterrupt()
            return reply([row(c) for c in codes])
        self.quotes=interrupt
        report,code=self.run_test()
        self.assertEqual((code,report['success_count'],report['uncertain_count'],report['uncollected_count']),(1,2,2,16))
        self.assertEqual(self.rows('SELECT status FROM integrated_quote_runs')[0]['status'],'uncertain')

    def test_time_budget_between_quotes_no_unsent_rows_invented(self):
        elapsed=[0]
        start=datetime.now(KST)
        def advance(codes):
            elapsed[0]+=61
            return reply([row(c) for c in codes])
        self.quotes=advance
        report,code=self.run_test(now=lambda:start+timedelta(seconds=elapsed[0]),clock=lambda:elapsed[0])
        self.assertEqual((code,report['reason'],report['success_count'],report['uncollected_count']),(1,'time_budget',2,18))
        self.assertEqual(len(self.rows('SELECT * FROM quote_observations')),2)

    def test_db_storage_failure_stops_quotes(self):
        with patch('kiwoom_collector.storage.Store.finish',side_effect=sqlite3.OperationalError('SENSITIVE_MESSAGE')):
            report,code=self.run_test()
        self.assertEqual((code,report['request_counts']['quotes']),(1,0))

    def test_quote_storage_failure_keeps_earlier_success_and_stops(self):
        from kiwoom_collector.scheduled_quotes import connection
        fail=[False]
        calls=[0]
        def quotes(codes):
            calls[0]+=1
            if calls[0]==2: fail[0]=True
            return reply([row(c) for c in codes])
        self.quotes=quotes
        class Proxy:
            def __init__(self,db): self.db=db
            def __getattr__(self,name): return getattr(self.db,name)
            def __enter__(self): self.db.__enter__(); return self
            def __exit__(self,*args): return self.db.__exit__(*args)
            def execute(self,sql,parameters=()):
                if fail[0] and sql.startswith('UPDATE quote_observations SET status='):
                    fail[0]=False
                    raise sqlite3.OperationalError('SENSITIVE_STORAGE_MESSAGE')
                return self.db.execute(sql,parameters)
        with patch('kiwoom_collector.scheduled_quotes.connection',side_effect=lambda *a,**kw: Proxy(connection(*a,**kw))):
            report,code=self.run_test()
        self.assertEqual((code,report['status'],report['success_count'],report['uncertain_count'],report['uncollected_count']),
                         (1,'uncertain',2,2,16))
        self.assertEqual(report['request_counts']['quotes'],2)
        self.assertEqual(len(self.rows('SELECT * FROM quote_top20_locks')),1)

    def test_request_gate_enforces_host_and_counts(self):
        client=IntegratedQuoteClient(self.credentials,clock=lambda:0)
        self.addCleanup(client.close)
        with self.assertRaises(KiwoomError):
            client.session.post('https://api.kiwoom.com/api/dostk/stkinfo',headers={'api-id':'ka10095'})
        self.session.post.assert_not_called()
        client.request_counts['ranking']=1
        with self.assertRaises(KiwoomError):
            client.session.post('https://mockapi.kiwoom.com/api/dostk/stkinfo',headers={'api-id':'ka00198'})
        client.request_counts['quotes']=10
        with self.assertRaises(KiwoomError):
            client.session.post('https://mockapi.kiwoom.com/api/dostk/stkinfo',headers={'api-id':'ka10095'})
        self.session.post.assert_not_called()

    def test_time_budget_after_auth_stops_before_ranking_http(self):
        elapsed=[0]
        dispatch=self.session.post.side_effect
        def advance(url,**kwargs):
            result=dispatch(url,**kwargs)
            if url.endswith('/oauth2/token'): elapsed[0]=91
            return result
        self.session.post.side_effect=advance
        report,code=self.run_test(clock=lambda:elapsed[0])
        self.assertEqual((code,report['reason'],report['request_counts']),(1,'time_budget',dict(auth=1,ranking=0,quotes=0)))

    def test_cli_real_settings_fail_without_new_db_or_http(self):
        with patch('kiwoom_collector.__main__.load_credentials',return_value=Credentials('fake','fake','real')),patch('sys.stderr'):
            code=main(['quote-integrated-test','--db',str(self.path),'--environment','mock'])
        self.assertEqual(code,1)
        self.assertFalse(self.path.exists())
        self.session.post.assert_not_called()

    def test_cli_requires_explicit_path_mock_and_qry_one(self):
        for argv in (['quote-integrated-test','--environment','mock'],
                     ['quote-integrated-test','--db',str(self.path)],
                     ['quote-integrated-test','--db',str(self.path),'--environment','real'],
                     ['quote-integrated-test','--db',str(self.path),'--environment','mock','--qry-tp','2']):
            with patch('sys.stderr'),self.assertRaises(SystemExit),patch('kiwoom_collector.__main__.load_credentials',side_effect=AssertionError('no credentials')):
                main(argv)
        self.session.post.assert_not_called()

    def test_cli_safe_summary_and_no_scheduler(self):
        output=io.StringIO(); errors=io.StringIO()
        with patch('kiwoom_collector.__main__.load_credentials',return_value=self.credentials), \
             patch('kiwoom_collector.__main__.collect_schedule',side_effect=AssertionError('no schedule')), \
             redirect_stdout(output),redirect_stderr(errors):
            code=main(['quote-integrated-test','--db',str(self.path),'--environment','mock','--qry-tp','1'])
        report=json.loads(output.getvalue())
        self.assertEqual((code,report['success_count']),(0,20))
        self.assertTrue(all(b-a>=1.2-1e-9 for a,b in zip(self.http_starts,self.http_starts[1:])))
        for sensitive in ('fake-key','fake-secret','DO_NOT_PRINT_TOKEN','SENSITIVE_MESSAGE'):
            self.assertNotIn(sensitive,output.getvalue()+errors.getvalue())

    def test_real_module_entrypoint_offline_summary(self):
        guard=self.folder/'guard'
        guard.mkdir()
        (guard/'sitecustomize.py').write_text('''import socket,requests,dotenv
from unittest.mock import Mock
dotenv.load_dotenv=lambda *a,**kw: False
def forbidden(*a,**kw): raise RuntimeError('NETWORK_FORBIDDEN')
socket.socket.connect=forbidden
socket.getaddrinfo=forbidden
def post(url,**kwargs):
    if url.endswith('/oauth2/token'):
        data=dict(return_code=0,token='FAKE_TOKEN_ONLY',token_type='bearer',expires_dt='20990101000000')
    elif kwargs['headers']['api-id']=='ka00198':
        data=dict(return_code=0,item_inq_rank=[dict(bigd_rank=str(i),stk_cd=f'{100000+i:06}',stk_nm='Fixture') for i in range(1,21)])
    else:
        data=dict(return_code=0,atn_stk_infr=[dict(stk_cd=c,stk_nm='Fixture',cur_prc='+100',flu_rt='+1',trde_qty='100',trde_prica='1',open_pric='+100',high_pric='+101',low_pric='+99',dt='20261023',cntr_tm='094000') for c in kwargs['json']['stk_cd'].split('|')])
    return Mock(status_code=200,headers={},json=Mock(return_value=data))
requests.Session=lambda:Mock(post=Mock(side_effect=post))
''',encoding='utf-8')
        root=Path(__file__).resolve().parents[1]
        env=os.environ.copy()
        env.update(PYTHONPATH=os.pathsep.join((str(guard),str(root))),PYTHONDONTWRITEBYTECODE='1',
                   PYTHONIOENCODING='utf-8',KIWOOM_APP_KEY='FAKE_KEY_ONLY',KIWOOM_SECRET_KEY='FAKE_SECRET_ONLY',KIWOOM_ENV='mock')
        child=subprocess.run([sys.executable,'-B','-m','kiwoom_collector','quote-integrated-test',
                              '--db',str(self.path),'--environment','mock','--qry-tp','1'],
                             cwd=root,env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(child.returncode,0,child.stderr)
        report=json.loads(child.stdout)
        self.assertEqual((report['success_count'],report['http_attempt_count']),(20,12))
        for sensitive in ('FAKE_TOKEN_ONLY','FAKE_KEY_ONLY','FAKE_SECRET_ONLY','NETWORK_FORBIDDEN'):
            self.assertNotIn(sensitive,child.stdout+child.stderr)

    def test_process_death_retains_running_and_reexecution_rejected(self):
        script='''import os,sys
from unittest.mock import Mock,patch
from kiwoom_collector.config import Credentials
from kiwoom_collector.integrated_test import integrated_test
def post(url,**kwargs):
    if url.endswith('/oauth2/token'):
        data=dict(return_code=0,token='fake',token_type='bearer',expires_dt='20990101000000')
    elif kwargs['headers']['api-id']=='ka00198':
        data=dict(return_code=0,item_inq_rank=[dict(bigd_rank=str(i),stk_cd=f'{100000+i:06}',stk_nm='Fixture') for i in range(1,21)])
    else: os._exit(39)
    return Mock(status_code=200,headers={},json=Mock(return_value=data))
with patch('kiwoom_collector.client.requests.Session') as factory:
    factory.return_value.post.side_effect=post
    integrated_test(sys.argv[1],Credentials('fake','fake'))
'''
        child=subprocess.run([sys.executable,'-B','-c',script,str(self.path)],capture_output=True,text=True,timeout=15)
        self.assertEqual(child.returncode,39,child.stderr)
        self.assertEqual(self.rows('SELECT status FROM integrated_quote_runs')[0]['status'],'running')
        self.assertEqual({r['status'] for r in self.rows('SELECT * FROM quote_observations')},{'running'})
        self.assertEqual(len(self.rows('SELECT * FROM quote_top20_locks')),1)
        with api_resource(self.path,'mock'): pass
        with self.assertRaises(ValueError): self.run_test()
        self.session.post.assert_not_called()
