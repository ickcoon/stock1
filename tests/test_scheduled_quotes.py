"""Fake HTTP and temporary databases only; production data is never opened."""
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
import json
import sqlite3
import tempfile
import threading
import subprocess
import sys
from unittest.mock import Mock, patch

import requests
import test_quotes as fixtures
from test_batch_quotes import row, reply
from kiwoom_collector.api_resource import api_resource
from kiwoom_collector.client import KST, Snapshot, KiwoomError
from kiwoom_collector.quotes import BatchQuoteClient, QuoteDeadlineReached, quote_test
from kiwoom_collector.scheduled_quotes import run_scheduled_quotes, migrate_scheduled_quotes, QuotePolicy
from kiwoom_collector.top20_quotes import execute
from kiwoom_collector.schedule import collect_schedule
from kiwoom_collector.__main__ import main


class ScheduledQuotesTests(fixtures.QuoteFixture):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)/'stock.db'
        self.at = datetime.now(KST).replace(microsecond=0)
        self.clock = self.at
        self.identifier = self.ranking()
        self.client = BatchQuoteClient(self.client.credentials)
        self.client.token = fixtures.Token('token',datetime.now(KST)+timedelta(hours=1))
        self.addCleanup(self.client.close)
        self.session.post.side_effect = lambda *a,**kw: reply([row(c) for c in kw['json']['stk_cd'].split('|')])

    def ranking(self, count=20, *, test=False, environment='mock', seconds=0):
        at = self.at+timedelta(seconds=seconds)
        with closing(fixtures.Store(self.path)) as store:
            identifier = store.begin(at,environment,'1',test_mode=test,scheduled_at=at)
            store.finish(identifier,Snapshot(at,[dict(bigd_rank=str(i),stk_cd=f'{100000+i:06}',stk_nm=f'Stock{i}') for i in range(1,count+1)]))
        return identifier

    def run_quotes(self, identifier=None, **options):
        values=dict(environment='mock',created_at=self.at,next_scheduled_at=self.at+timedelta(minutes=10),now=lambda:self.clock)
        values.update(options)
        return run_scheduled_quotes(self.path,self.client,identifier or self.identifier,**values)

    def rows(self, sql):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory=sqlite3.Row
            return [dict(r) for r in db.execute(sql)]

    def test_twenty_targets_ten_calls_same_collection_and_modes(self):
        report=self.run_quotes()
        self.assertEqual((report['status'],report['observed'],self.session.post.call_count),('complete',20,10))
        observations=self.rows('SELECT * FROM quote_observations')
        self.assertEqual({r['test_mode'] for r in observations},{0})
        self.assertEqual({r['collection_id'] for r in observations},{self.identifier})
        self.assertTrue(all(r['request_started_at'] and r['request_finished_at'] for r in observations))
        self.assertTrue(all(json.loads(r['normalized_json'])['api_time_kind']=='execution_time' for r in observations))
        self.assertEqual(self.rows('SELECT * FROM quote_top20_locks'),[])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_nineteen_targets_ten_calls_and_duplicate_does_not_resume(self):
        identifier=self.ranking(19,seconds=1)
        options=dict(created_at=self.at+timedelta(seconds=1),now=lambda:self.at+timedelta(seconds=1))
        self.assertEqual(self.run_quotes(identifier,**options)['observed'],19)
        self.assertEqual(self.session.post.call_count,10)
        self.assertEqual(self.run_quotes(identifier,**options)['reason'],'already_claimed')
        self.assertEqual(self.session.post.call_count,10)

    def test_partial_missing_stock_is_not_zero_or_old_quote(self):
        self.session.post.side_effect=lambda *a,**kw: reply([row(kw['json']['stk_cd'].split('|')[0])])
        self.assertEqual(self.run_quotes()['status'],'partial')
        self.assertEqual(len(self.rows("SELECT * FROM quote_observations WHERE status='error'")),10)
        missing=self.rows("SELECT * FROM quote_observations WHERE status='error'")[0]
        self.assertIn('missing_stock',json.loads(missing['issues_json']))
        self.assertIsNone(json.loads(missing['normalized_json'])['change_percent'])
        self.assertEqual(self.rows('SELECT status FROM collections')[0]['status'],'success')

    def test_all_stock_failures_are_error_not_partial(self):
        self.session.post.side_effect=lambda *a,**kw: reply([])
        self.assertEqual(self.run_quotes()['status'],'error')
        self.assertEqual(self.rows('SELECT status FROM quote_batches')[0]['status'],'error')
        self.assertEqual(self.session.post.call_count,10)

    def test_quote_failure_does_not_change_ranking_or_stop_next_slot(self):
        at=self.at.replace(hour=9,minute=20,second=0)
        clock=[at]
        with closing(fixtures.Store(Path(self.path.parent)/'flow.db')) as store:
            client=Mock()
            client.fetch_once.return_value=Snapshot(at,[dict(bigd_rank='1',stk_cd='100001',stk_nm='One')])
            def sleep(seconds):
                clock[0]+=timedelta(seconds=seconds)
                if clock[0]>at+timedelta(minutes=10): raise KeyboardInterrupt()
            with patch('kiwoom_collector.schedule.run_scheduled_quotes',side_effect=ValueError('fake')) as followup:
                with self.assertRaises(KeyboardInterrupt):
                    collect_schedule(client,store,'mock',now=lambda:clock[0],sleep=sleep,with_quotes=True)
                self.assertEqual(followup.call_count,2)
                self.assertEqual(store.status()['successes'],2)

    def test_auth_rate_limit_http_failure_stop_without_retries(self):
        for index,code in enumerate((1700,8005,8104)):
            identifier=self.identifier if index==0 else self.ranking(seconds=index)
            self.session.post.side_effect=None
            response=Mock(status_code=200,headers={})
            response.json.return_value={'return_code':code}
            self.session.post.return_value=response
            self.session.post.reset_mock()
            self.assertEqual(self.run_quotes(identifier,created_at=self.at+timedelta(seconds=index),now=lambda:self.at+timedelta(seconds=index))['status'],'stopped')
            self.assertEqual(self.session.post.call_count,1)

    def test_no_auth_token_authentication_failure_stops(self):
        self.client.token=None
        self.session.post.side_effect=requests.Timeout()
        self.assertEqual(self.run_quotes()['status'],'stopped')
        self.assertEqual(self.session.post.call_count,1)
        self.assertTrue(all(r['request_started_at'] is None for r in self.rows('SELECT * FROM quote_observations')))

    def test_deadline_prevents_start_and_continue(self):
        self.assertEqual(self.run_quotes(next_scheduled_at=self.at+timedelta(seconds=90))['reason'],'time_budget')
        self.session.post.assert_not_called()
        identifier=self.ranking(seconds=1)
        self.clock=self.at+timedelta(seconds=1)
        def response(*args,**kwargs):
            self.clock+=timedelta(seconds=61)
            return reply([row(c) for c in kwargs['json']['stk_cd'].split('|')])
        self.session.post.side_effect=response
        report=self.run_quotes(identifier,created_at=self.at+timedelta(seconds=1))
        self.assertEqual((report['reason'],report['observed']),('time_budget',2))
        self.assertEqual(self.session.post.call_count,1)
        self.assertEqual(self.run_quotes(identifier,created_at=self.at+timedelta(seconds=1))['reason'],'already_claimed')

    def test_client_checks_budget_after_authentication_before_send(self):
        self.client.request_deadline=datetime.now(KST)+timedelta(seconds=20)
        with self.assertRaises(QuoteDeadlineReached):
            self.client.fetch_quotes(['100001'])
        self.session.post.assert_not_called()

    def test_deadline_expires_during_authentication_no_quote_sent(self):
        self.client.token=None
        def authenticate(**kwargs):
            self.client.request_deadline=datetime.now(KST)+timedelta(seconds=20)
        with patch.object(self.client,'authenticate',side_effect=authenticate) as auth:
            self.assertEqual(self.run_quotes()['reason'],'deadline_before_send')
            auth.assert_called_once()
        self.session.post.assert_not_called()
        self.assertEqual(self.rows('SELECT status FROM quote_requests')[0]['status'],'skipped')

    def test_old_wrong_flow_test_parent_and_environment_rejected(self):
        self.clock=self.at+timedelta(minutes=4)
        self.assertEqual(self.run_quotes()['reason'],'stale_ranking')
        self.clock=self.at
        with self.assertRaises(ValueError): self.run_quotes(environment='real')
        with self.assertRaises(ValueError): self.run_quotes(created_at=self.at-timedelta(seconds=1))
        identifier=self.ranking(test=True,seconds=1)
        with self.assertRaises(ValueError): self.run_quotes(identifier,created_at=self.at+timedelta(seconds=1),now=lambda:self.at+timedelta(seconds=1))
        self.session.post.assert_not_called()

    def test_migration_preserves_test_rows_requests_and_is_idempotent(self):
        quote_test(self.path,self.client,self.identifier,limit=2,api_id='ka10095')
        old={name:self.rows('SELECT * FROM '+name) for name in ('quote_batches','quote_observations','quote_requests','collections','ranking_records')}
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE')
            migrate_scheduled_quotes(db)
            migrate_scheduled_quotes(db)
            db.commit()
        for name,rows in old.items():
            new=self.rows('SELECT * FROM '+name)
            for original,current in zip(rows,new):
                self.assertEqual(original,{k:current[k] for k in original})
        self.session.post.reset_mock()
        self.assertEqual(self.run_quotes()['observed'],20)
        self.assertEqual(len(self.rows('SELECT * FROM quote_observations WHERE test_mode=1')),2)
        self.assertEqual(len(self.rows('SELECT * FROM quote_observations WHERE test_mode=0')),20)
        # Existing explicit test CLI still works after the schema extension.
        self.assertEqual(execute(self.path,self.client,self.identifier)[1],0)

    def test_migration_rollback_restores_original_schema(self):
        before=self.path.read_bytes()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE')
            migrate_scheduled_quotes(db)
            db.rollback()
        self.assertEqual(self.path.read_bytes(),before)

    def test_transport_uncertainty_retains_lock_and_blocks_other_quotes(self):
        self.session.post.side_effect=requests.Timeout()
        report=self.run_quotes()
        self.assertEqual(report['status'],'uncertain')
        self.assertEqual(self.rows('SELECT status FROM quote_requests')[0]['status'],'uncertain')
        self.assertEqual(len(self.rows('SELECT * FROM quote_top20_locks')),1)
        with self.assertRaises(ValueError): execute(self.path,self.client,self.identifier)
        with self.assertRaises(ValueError): quote_test(self.path,self.client,self.identifier)
        self.assertEqual(self.session.post.call_count,1)

    def test_ctrl_c_preserves_unknown_and_successful_observations(self):
        self.session.post.side_effect=[reply([row('100001'),row('100002')]),KeyboardInterrupt()]
        with self.assertRaises(KeyboardInterrupt): self.run_quotes()
        self.assertEqual(len(self.rows("SELECT * FROM quote_observations WHERE status='success'")),2)
        self.assertEqual(self.rows('SELECT status FROM scheduled_quote_runs')[0]['status'],'uncertain')
        self.assertEqual(len(self.rows('SELECT * FROM quote_top20_locks')),1)
        self.assertEqual(self.run_quotes()['reason'],'already_claimed')
        self.assertEqual(self.session.post.call_count,2)

    def test_residual_running_lock_is_not_changed_or_replayed(self):
        self.run_quotes(next_scheduled_at=self.at+timedelta(seconds=90))
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE scheduled_quote_runs SET status='running'")
            batch=db.execute('SELECT batch_id FROM scheduled_quote_runs').fetchone()[0]
            db.execute("UPDATE quote_batches SET status='running',finished_at=NULL")
            db.execute("INSERT INTO quote_top20_locks VALUES('mock',?)",(batch,))
            db.commit()
        self.assertEqual(self.run_quotes()['reason'],'already_claimed')
        self.assertEqual(self.rows('SELECT status FROM scheduled_quote_runs')[0]['status'],'running')
        self.assertEqual(len(self.rows('SELECT * FROM quote_top20_locks')),1)

    def test_resource_lock_excludes_threads_and_releases_without_db_recovery(self):
        errors=[]
        def other():
            try:
                with api_resource(self.path,'mock'): errors.append('unexpected')
            except ValueError: errors.append('blocked')
        with api_resource(self.path,'mock'):
            thread=threading.Thread(target=other)
            thread.start(); thread.join()
        self.assertEqual(errors,['blocked'])
        with api_resource(self.path,'mock'): pass

    def test_process_death_preserves_running_and_os_lock_is_released(self):
        script = '''import os,sys
from datetime import datetime,timedelta
from kiwoom_collector.client import KST
from kiwoom_collector.config import Credentials
from kiwoom_collector.quotes import BatchQuoteClient
from kiwoom_collector.scheduled_quotes import run_scheduled_quotes
client=BatchQuoteClient(Credentials('fake','fake'))
client.fetch_quotes=lambda codes: os._exit(39)
at=datetime.fromisoformat(sys.argv[3])
run_scheduled_quotes(sys.argv[1],client,sys.argv[2],environment='mock',created_at=at,
 next_scheduled_at=at+timedelta(minutes=10),now=lambda:at)
'''
        child=subprocess.run([sys.executable,'-B','-c',script,str(self.path),self.identifier,self.at.isoformat()],
                             capture_output=True,text=True,timeout=15)
        self.assertEqual(child.returncode,39,child.stderr)
        self.assertEqual(self.rows('SELECT status FROM scheduled_quote_runs')[0]['status'],'running')
        self.assertEqual({r['status'] for r in self.rows('SELECT * FROM quote_observations')},{'running'})
        with api_resource(self.path,'mock'): pass
        self.assertEqual(self.run_quotes()['reason'],'already_claimed')
        identifier=self.ranking(seconds=1)
        self.assertEqual(self.run_quotes(identifier,created_at=self.at+timedelta(seconds=1),now=lambda:self.at+timedelta(seconds=1))['reason'],'unresolved_lock')
        self.session.post.assert_not_called()

    def test_resource_lock_excludes_another_process(self):
        script='''import sys
from kiwoom_collector.api_resource import api_resource
try:
    with api_resource(sys.argv[1],'mock'): pass
except ValueError:
    sys.exit(11)
'''
        with api_resource(self.path,'mock'):
            child=subprocess.run([sys.executable,'-B','-c',script,str(self.path)],capture_output=True,text=True,timeout=15)
        self.assertEqual(child.returncode,11,child.stderr)

    def test_mode_constraints_and_existing_top20_history_survive_migration(self):
        execute(self.path,self.client,self.identifier)
        old=self.rows('SELECT * FROM quote_observations')
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE')
            migrate_scheduled_quotes(db)
            db.commit()
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            batch=old[0]['batch_id']
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO quote_observations(batch_id,collection_id,rank,requested_code,api_id,test_mode,status,attempt) VALUES(?,?,1,'100001','ka10095',0,'running',1)",
                           (batch,self.identifier))
        self.assertEqual(self.rows('SELECT * FROM quote_observations'),old)

    def test_scheduler_success_passes_new_id_failure_and_empty_do_not(self):
        for index,kind in enumerate(('success','failure','empty')):
            folder=Path(self.path.parent)/kind
            with closing(fixtures.Store(folder/'db.sqlite')) as store:
                at=self.at.replace(hour=9,minute=20,second=0)
                client=Mock()
                client.fetch_once.side_effect=KiwoomError('fake') if kind=='failure' else None
                client.fetch_once.return_value=Snapshot(at,[] if kind=='empty' else [dict(bigd_rank='1',stk_cd='100001',stk_nm='One')])
                with patch('kiwoom_collector.schedule.run_scheduled_quotes',return_value=dict(status='complete',reason=None)) as followup:
                    with self.assertRaises(KeyboardInterrupt):
                        collect_schedule(client,store,'mock',now=lambda:at,sleep=Mock(side_effect=KeyboardInterrupt()),with_quotes=True)
                    self.assertEqual(followup.call_count,1 if kind=='success' else 0)
                    if kind=='success':
                        identifier=store.db.execute('SELECT id FROM collections').fetchone()[0]
                        self.assertEqual(followup.call_args.args[2],identifier)

    def test_scheduler_to_fake_http_end_to_end(self):
        at=self.at.replace(hour=9,minute=20,second=0)
        # Before market open the simulated clock is ahead of the real token clock.
        self.client.token=fixtures.Token('token',max(self.at,at)+timedelta(hours=1))
        with closing(fixtures.Store(Path(self.path.parent)/'scheduled.db')) as store:
            snapshot=Snapshot(at,[dict(bigd_rank=str(i),stk_cd=f'{100000+i:06}',stk_nm=f'Stock{i}') for i in range(1,21)])
            with patch.object(self.client,'fetch_once',return_value=snapshot), \
                 patch('kiwoom_collector.quotes.datetime',wraps=datetime) as times:
                times.now.return_value=at
                with self.assertRaises(KeyboardInterrupt):
                    collect_schedule(self.client,store,'mock',now=lambda:at,sleep=Mock(side_effect=KeyboardInterrupt()),with_quotes=True)
            identifier=store.db.execute('SELECT id FROM collections').fetchone()[0]
            observed=store.db.execute('SELECT collection_id,test_mode FROM quote_observations').fetchall()
            self.assertEqual(len(observed),20)
            self.assertEqual({(r['collection_id'],r['test_mode']) for r in observed},{(identifier,0)})
            self.assertEqual(self.session.post.call_count,10)

    def test_cli_option_scope_and_default_compatibility(self):
        for argv in (['once','--with-top20-quotes'], ['collect','--with-top20-quotes'], ['collect','--test-now','--with-top20-quotes']):
            with patch('sys.stderr'),self.assertRaises(SystemExit): main(argv)
        for enabled in (False,True):
            with patch('kiwoom_collector.__main__.load_credentials',return_value=self.client.credentials), \
                 patch('kiwoom_collector.__main__.KiwoomClient') as normal, \
                 patch('kiwoom_collector.__main__.BatchQuoteClient') as batch, \
                 patch('kiwoom_collector.__main__.collect_schedule') as scheduler,patch('builtins.print'):
                argv=['collect','--schedule','--db',str(self.path)]
                if enabled: argv.append('--with-top20-quotes')
                self.assertEqual(main(argv),0)
                self.assertEqual(bool(scheduler.call_args.kwargs.get('with_quotes')),enabled)
                self.assertEqual(batch.call_count,int(enabled))
                self.assertEqual(normal.call_count,int(not enabled))

    def test_policy_values_are_bounded(self):
        for options in (dict(max_age_seconds=-1),dict(spacing_seconds=.1),dict(max_duration_seconds=float('inf'))):
            with self.assertRaises(ValueError): QuotePolicy(**options)
