"""TOP20 planning/execution uses temporary databases and fake HTTP only."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch

import test_quotes as fixtures
from test_batch_quotes import row, reply
import test_batch_quotes as batch_fixtures
from kiwoom_collector.top20_quotes import dry_run, execute
from kiwoom_collector.quotes import BatchQuoteClient, quote_test
from kiwoom_collector.__main__ import main


class Top20Tests(fixtures.QuoteFixture):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)/'stock1.db'
        at = fixtures.datetime(2026,10,22,9,40,tzinfo=fixtures.KST)
        with closing(fixtures.Store(self.path)) as store:
            self.identifier = store.begin(at,'mock','1',scheduled_at=at)
            # Nonconsecutive stock codes, shuffled source order.
            records = [{'bigd_rank':str(i),'stk_cd':f'{100000+i:06d}','stk_nm':f'stock{i}'} for i in range(20,0,-1)]
            store.finish(self.identifier,fixtures.Snapshot(at,records))
        self.client = BatchQuoteClient(self.client.credentials)
        self.client.token = fixtures.Token('token', fixtures.datetime.now(fixtures.KST)+fixtures.timedelta(hours=1))
        self.addCleanup(self.client.close)
        self.session.post.side_effect = lambda *a,**kw: reply([row(c) for c in reversed(kw['json']['stk_cd'].split('|'))])

    def rows(self, sql):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory=sqlite3.Row
            return [dict(r) for r in db.execute(sql)]

    def original(self):
        with closing(sqlite3.connect(self.path)) as db:
            return [line for line in db.iterdump() if 'quote_' not in line]

    def test_dry_run_does_not_write_or_load_credentials_or_http(self):
        before = self.path.read_bytes()
        files = sorted(p.name for p in self.path.parent.iterdir())
        with patch('kiwoom_collector.__main__.load_credentials',side_effect=AssertionError('no credentials')), \
             patch('kiwoom_collector.__main__.BatchQuoteClient',side_effect=AssertionError('no client')), \
             patch('kiwoom_collector.top20_quotes.migrate_quotes',side_effect=AssertionError('no migration')), \
             patch('builtins.print') as output:
            code=main(['quote-top20-test','--dry-run','--collection-id',self.identifier,'--db',str(self.path)])
        plan=json.loads(output.call_args.args[0])
        self.assertEqual(code,0)
        self.assertEqual(plan['actual_api_calls'],0)
        self.assertEqual(plan['expected_quote_requests'],10)
        self.assertEqual(plan['requests'][0]['codes'],['100001','100002'])
        self.assertEqual(plan['requests'][-1]['ranks'],[19,20])
        self.assertEqual(before,self.path.read_bytes())
        self.assertEqual(files,sorted(p.name for p in self.path.parent.iterdir()))
        self.session.post.assert_not_called()

    def test_existing_batch_success_is_kept_and_remaining_eighteen_split(self):
        quote_test(self.path,self.client,self.identifier,limit=2,api_id='ka10095')
        old=self.rows('SELECT * FROM quote_observations ORDER BY rank')
        plan=dry_run(self.path,self.identifier)
        self.assertEqual((len(plan['skipped']),plan['expected_quote_requests']),(2,9))
        self.assertEqual(plan['requests'][0]['ranks'],[3,4])
        before=self.original()
        result,code=execute(self.path,self.client,self.identifier)
        self.assertEqual((result['completed_requests'],code),(9,0))
        saved=self.rows('SELECT * FROM quote_observations WHERE rank<=2 ORDER BY rank')
        for original, current in zip(old,saved):
            self.assertEqual(current.pop('attempt'),0)
            self.assertIsNone(current.pop('request_id'))
            self.assertEqual(original,current)
        self.assertEqual(before,self.original())
        self.session.post.reset_mock()
        self.assertEqual(execute(self.path,self.client,self.identifier)[0]['status'],'skipped')
        self.session.post.assert_not_called()

    def test_top20_fixed_once_request_links_and_order_mapping(self):
        before=self.original()
        result,code=execute(self.path,self.client,self.identifier)
        self.assertEqual((code,result['completed_requests'],result['actual_quote_requests']),(0,10,10))
        observations=self.rows('SELECT * FROM quote_observations ORDER BY rank')
        self.assertEqual(len(observations),20)
        for observation in observations:
            self.assertEqual(json.loads(observation['raw_json'])['stk_cd'],observation['requested_code'])
            self.assertIsNotNone(observation['request_id'])
        self.assertEqual(len(self.rows('SELECT * FROM quote_requests')),10)
        self.assertEqual(before,self.original())
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_snapshot_is_not_reread_between_requests(self):
        calls=[]
        def post(*args,**kwargs):
            calls.append(kwargs['json']['stk_cd'])
            if len(calls)==1:
                with closing(sqlite3.connect(self.path)) as db:
                    db.execute("UPDATE ranking_records SET stk_cd='999999' WHERE rank=20")
                    db.commit()
            return reply([row(c) for c in kwargs['json']['stk_cd'].split('|')])
        self.session.post.side_effect=post
        execute(self.path,self.client,self.identifier)
        self.assertEqual(calls[-1],'100019|100020')

    def test_minimum_spacing_configurable_and_token_reused(self):
        clock=[100.0]
        starts=[]
        def sleep(seconds):
            clock[0]+=seconds
        def post(*args,**kwargs):
            starts.append(clock[0])
            return reply([row(c) for c in kwargs['json']['stk_cd'].split('|')])
        self.session.post.side_effect=post
        with patch('kiwoom_collector.quotes.time.monotonic',side_effect=lambda:clock[0]), \
             patch('kiwoom_collector.quotes.time.sleep',side_effect=sleep):
            execute(self.path,self.client,self.identifier,spacing=1.75)
        self.assertEqual(len(starts),10)
        self.assertTrue(all(b-a>=1.75-1e-9 for a,b in zip(starts,starts[1:])))
        self.assertEqual(self.session.post.call_count,10)

    def test_limits_and_auth_stop_without_retries_and_resume_unsent(self):
        self.session.post.side_effect=[reply([row('100001'),row('100002')]),fixtures.response(status=429)]
        result,code=execute(self.path,self.client,self.identifier)
        self.assertEqual((code,result['completed_requests'],result['unsent_requests']),(1,2,8))
        self.assertEqual(self.session.post.call_count,2)
        plan=dry_run(self.path,self.identifier)
        self.assertEqual((plan['expected_quote_requests'],len(plan['skipped'])),(8,4))
        retry=dry_run(self.path,self.identifier,recollect='failed')
        self.assertEqual(retry['expected_quote_requests'],9)
        self.assertEqual(retry['requests'][0]['ranks'],[3,4])

    def test_auth_and_rate_error_classification_including_embedded_codes(self):
        for response in (fixtures.response(status=401),fixtures.response(status=403),
                         fixtures.response({'return_code':1700}),fixtures.response({'return_code':8104}),
                         fixtures.response({'return_code':3,'return_msg':'CODE=8005 secret'})):
            self.session.post.side_effect=None
            self.session.post.return_value=response
            self.session.post.reset_mock()
            # Each case is explicitly recollected, while previous data stays intact.
            result,code=execute(self.path,self.client,self.identifier,recollect='all')
            self.assertEqual((code,result['completed_requests']),(1,1))
            self.assertEqual(self.session.post.call_count,1)
            self.assertNotIn('secret',str(result))

    def test_partial_missing_and_server_failure_are_independent(self):
        self.session.post.side_effect=[reply([row('100001')]),fixtures.response(status=500)]+[
            reply([row(f'{100000+i:06d}'),row(f'{100001+i:06d}')]) for i in range(5,20,2)]
        result,code=execute(self.path,self.client,self.identifier)
        self.assertEqual((result['status'],code,result['completed_requests']),('partial',1,10))
        self.assertEqual(self.rows('SELECT status FROM quote_observations WHERE rank=2')[0]['status'],'error')
        self.assertIn('missing_stock',self.rows('SELECT issues_json FROM quote_observations WHERE rank=2')[0]['issues_json'])

    def test_ctrl_c_preserves_success_and_allows_unsent_resume(self):
        self.session.post.side_effect=[reply([row('100001'),row('100002')]),KeyboardInterrupt()]
        with self.assertRaises(KeyboardInterrupt):
            execute(self.path,self.client,self.identifier)
        self.assertEqual(len(self.rows('SELECT * FROM quote_top20_locks')),0)
        self.assertEqual(len(self.rows("SELECT * FROM quote_observations WHERE status='success'")),2)
        self.assertEqual(dry_run(self.path,self.identifier)['expected_quote_requests'],8)

    def test_explicit_recollection_appends_and_old_client_still_works(self):
        execute(self.path,self.client,self.identifier)
        old=self.rows('SELECT * FROM quote_observations ORDER BY rank')
        execute(self.path,self.client,self.identifier,recollect='all')
        self.assertEqual(self.rows('SELECT * FROM quote_observations WHERE attempt=0 ORDER BY rank'),old)
        self.assertEqual(len(self.rows('SELECT * FROM quote_observations')),40)
        self.session.post.reset_mock()
        quote_test(self.path,self.client,self.identifier,limit=2,api_id='ka10095')
        self.session.post.assert_not_called()

    def test_environment_invalid_spacing_and_missing_db_fail_without_writes(self):
        before=self.path.read_bytes()
        with self.assertRaises(ValueError):
            dry_run(self.path,self.identifier,environment='real')
        for value in (0,1.0,float('nan'),float('inf')):
            with self.assertRaises(ValueError):
                dry_run(self.path,self.identifier,spacing=value)
        with self.assertRaises(sqlite3.OperationalError):
            dry_run(self.path.parent/'missing.db',self.identifier)
        self.assertEqual(before,self.path.read_bytes())
        self.assertFalse((self.path.parent/'missing.db').exists())

    def test_dry_run_includes_uncheckpointed_wal_and_preserves_all_source_files(self):
        with closing(sqlite3.connect(self.path)) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute("UPDATE ranking_records SET stk_nm='committed-in-wal' WHERE rank=1")
            writer.commit()
            before={p.name:p.read_bytes() for p in self.path.parent.iterdir()}
            plan=dry_run(self.path,self.identifier)
            self.assertEqual(plan['targets'][0]['stk_nm'],'committed-in-wal')
            self.assertEqual(before,{p.name:p.read_bytes() for p in self.path.parent.iterdir()})

    def test_dry_snapshot_change_is_rejected(self):
        original=Path.read_bytes
        count=[0]
        def read(path):
            data=original(path)
            if path==self.path:
                count[0]+=1
                if count[0]==2:
                    return data+b'changed'
            return data
        with patch.object(Path,'read_bytes',read),self.assertRaises(ValueError):
            dry_run(self.path,self.identifier)
        self.session.post.assert_not_called()

    def test_legacy_schema_upgrade_and_transaction_rollback(self):
        batch_fixtures.BatchTests.legacy_schema(self)
        old=self.rows('SELECT * FROM quote_observations')[0]
        from kiwoom_collector.top20_quotes import migrate_top20
        def fail(db):
            migrate_top20(db)
            raise sqlite3.OperationalError('injected')
        with closing(sqlite3.connect(self.path)) as db:
            before=list(db.iterdump())
        with patch('kiwoom_collector.top20_quotes.migrate_top20',side_effect=fail),self.assertRaises(sqlite3.OperationalError):
            execute(self.path,self.client,self.identifier)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(before,list(db.iterdump()))
        self.session.post.assert_not_called()
        execute(self.path,self.client,self.identifier)
        saved=self.rows("SELECT * FROM quote_observations WHERE api_id='ka10007'")[0]
        self.assertEqual(saved.pop('attempt'),0)
        self.assertIsNone(saved.pop('normalized_json'))
        self.assertIsNone(saved.pop('request_id'))
        self.assertEqual(saved,old)

    def test_concurrent_top20_and_legacy_client_are_blocked(self):
        attempts=[0]
        def post(*args,**kwargs):
            attempts[0]+=1
            with self.assertRaises(ValueError):
                execute(self.path,self.client,self.identifier)
            with self.assertRaises(ValueError):
                quote_test(self.path,self.client,self.identifier,limit=2,api_id='ka10095')
            plan=dry_run(self.path,self.identifier)
            self.assertIsNotNone(plan['blocked_by_batch_id'])
            return reply([row(c) for c in kwargs['json']['stk_cd'].split('|')])
        self.session.post.side_effect=post
        execute(self.path,self.client,self.identifier)
        self.assertEqual(attempts[0],10)

    def test_cli_execution_closes_client_and_never_uses_store(self):
        with patch('kiwoom_collector.__main__.load_credentials',return_value=self.client.credentials), \
             patch('kiwoom_collector.__main__.BatchQuoteClient',return_value=self.client), \
             patch('kiwoom_collector.__main__.Store',side_effect=AssertionError('no Store')),patch('builtins.print'):
            code=main(['quote-top20-test','--collection-id',self.identifier,'--db',str(self.path)])
        self.assertEqual(code,0)
        self.session.close.assert_called()

    def test_odd_targets_and_new_cli_scope_validation(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('DELETE FROM ranking_records WHERE rank=20')
            db.commit()
        plan=dry_run(self.path,self.identifier)
        self.assertEqual((plan['expected_quote_requests'],len(plan['requests'][-1]['codes'])),(10,1))
        for argv in (['quote-top20-test'],['quote-top20-test','--collection-id','x','--request-spacing','1'],
                     ['collect','--recollect','all'],['quote-top20-test','--collection-id','x','--schedule']):
            with self.assertRaises(SystemExit),patch('sys.stderr'):
                main(argv)

    def test_observed_alphanumeric_codes_are_passed_without_substitution(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE ranking_records SET stk_cd='0035S0' WHERE rank=3")
            db.execute("UPDATE ranking_records SET stk_cd='0158S0' WHERE rank=9")
            db.commit()
        plan=dry_run(self.path,self.identifier)
        self.assertEqual(plan['requests'][1]['stk_cd'],'0035S0|100004')
        result,code=execute(self.path,self.client,self.identifier)
        self.assertEqual(code,0)
        self.assertEqual(self.rows('SELECT requested_code FROM quote_observations WHERE rank=9')[0]['requested_code'],'0158S0')
