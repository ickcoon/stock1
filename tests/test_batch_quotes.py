"""No live credentials or network: ka10095 contracts and legacy migration."""
from contextlib import closing
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import requests
import test_quotes as fixtures
from kiwoom_collector.quotes import BatchQuoteClient, quote_test, normalize_quote, VALUE_FIELDS
from kiwoom_collector.__main__ import main


def row(code):
    data = fixtures.body(code)
    data['dt'] = data.pop('date')
    data['cntr_tm'] = data.pop('tm')
    del data['return_code']
    return data


def reply(rows):
    return fixtures.response({'return_code': 0, 'atn_stk_infr': rows})


class BatchTests(fixtures.QuoteFixture):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'stock1.db'
        at = fixtures.datetime(2026,10,22,9,20,tzinfo=fixtures.KST)
        with closing(fixtures.Store(self.path)) as store:
            self.identifier = store.begin(at,'mock','1',scheduled_at=at)
            store.finish(self.identifier, fixtures.Snapshot(at,[
                {'bigd_rank':'1','stk_cd':'005930','stk_nm':'삼성전자'},
                {'bigd_rank':'2','stk_cd':'000660','stk_nm':'SK하이닉스'}]))
        self.client = BatchQuoteClient(self.client.credentials)
        self.client.token = fixtures.Token('token', fixtures.datetime.now(fixtures.KST) + fixtures.timedelta(hours=1))
        self.addCleanup(self.client.close)

    rows = fixtures.QuoteStorageTests.rows
    original_dump = fixtures.QuoteStorageTests.original_dump

    def run_batch(self):
        return quote_test(self.path, self.client, self.identifier, limit=2, api_id='ka10095')

    def test_reordered_response_maps_codes_and_preserves_times(self):
        before = self.original_dump()
        first, second = row('005930'), row('000660')
        second['cur_prc'] = '+123456'
        self.session.post.return_value = reply([second, first])
        report, code = self.run_batch()
        self.assertEqual((report['status'], code), ('success', 0))
        call = self.session.post.call_args
        self.assertEqual(call.args[0], 'https://mockapi.kiwoom.com/api/dostk/stkinfo')
        self.assertEqual(call.kwargs['json'], {'stk_cd': '005930|000660'})
        self.assertEqual(call.kwargs['headers']['api-id'], 'ka10095')
        saved = self.rows("SELECT * FROM quote_observations ORDER BY rank")
        self.assertEqual([r['cur_prc'] for r in saved], ['+070000', '+123456'])
        self.assertEqual(json.loads(saved[0]['raw_json'])['cntr_tm'], '093000')
        self.assertIsNone(saved[0]['tm'])
        self.assertEqual(saved[0]['request_started_at'], saved[1]['request_started_at'])
        self.assertEqual(json.loads(saved[0]['normalized_json'])['api_time_kind'], 'execution_time')
        request = self.rows('SELECT * FROM quote_requests')[0]
        self.assertEqual(request['status'], 'success')
        self.assertEqual(request['started_at'], saved[0]['request_started_at'])
        self.assertEqual(before, self.original_dump())

    def test_missing_stock_partial_fields_and_duplicate_rows(self):
        cases = [([row('005930')], ['success','error'], 'missing_stock'),
                 ([row('005930'),row('000660'),row('000660')], ['success','error'], 'duplicate_stock_code')]
        for rows, states, issue in cases:
            with self.subTest(issue=issue):
                self.session.post.return_value = reply(rows)
                result = self.client.fetch_quotes(['005930','000660'])
                self.assertEqual([q.status for q in result.values()], states)
                self.assertIn(issue, result['000660'].issues)
        data = row('005930')
        del data['trde_prica']
        data['dt'] = '20260230'
        self.session.post.return_value = reply([data,row('000660')])
        report, code = self.run_batch()
        self.assertEqual((report['status'],code), ('partial',1))
        record = self.rows('SELECT * FROM quote_observations WHERE rank=1')[0]
        self.assertIsNone(record['trde_prica'])
        self.assertIsNone(json.loads(record['normalized_json'])['turnover_won'])

    def test_batch_errors_do_not_fallback_or_leak(self):
        for response in [fixtures.response({'return_code':8104,'return_msg':'secret'}),
                         fixtures.response({'return_code':1700}), fixtures.response(status=429),
                         fixtures.response({'return_code':0}), reply([row('999999')]),
                         reply([{'cur_prc':'10'}]), reply(['bad']), fixtures.response([]),
                         fixtures.response({'return_code':True}),
                         fixtures.response({'return_code':0,'atn_stk_infr':[]},headers={'cont-yn':'Y'})]:
            self.session.post.return_value = response
            self.session.post.reset_mock()
            values = self.client.fetch_quotes(['005930','000660'])
            self.assertTrue(all(q.status == 'error' and not q.raw for q in values.values()))
            self.assertNotIn('secret', str(values))
            self.assertEqual(self.session.post.call_count, 1)
        self.session.post.return_value = fixtures.response({'return_code':8104})
        before = self.original_dump()
        report, code = self.run_batch()
        self.assertEqual((report['status'], code), ('error',1))
        self.assertEqual(before,self.original_dump())
        self.assertIn('mock_unsupported',self.rows('SELECT issues_json FROM quote_observations')[0]['issues_json'])

    def test_repeat_and_concurrent_execution_never_replays_failed_requests(self):
        def during_request(*args, **kwargs):
            report, code = self.run_batch()
            self.assertEqual(report['status'], 'skipped')
            return fixtures.response(status=500)
        self.session.post.side_effect = during_request
        self.run_batch()
        self.session.post.reset_mock()
        result, code = self.run_batch()
        self.assertEqual((result['status'],result['skipped'],code), ('skipped',2,0))
        self.session.post.assert_not_called()
        self.assertEqual(len(self.rows('SELECT * FROM quote_requests')),1)

    def test_single_api_and_batch_api_do_not_collide(self):
        self.session.post.return_value = fixtures.response()
        quote_test(self.path, self.client, self.identifier)
        self.session.post.return_value = reply([row('005930'),row('000660')])
        self.assertEqual(self.run_batch()[1],0)
        self.assertEqual(len(self.rows('SELECT * FROM quote_observations')),3)

    def test_environment_guards_before_writes_and_real_host(self):
        with self.assertRaises(ValueError):
            quote_test(self.path,self.client,self.identifier,environment='real',limit=2,api_id='ka10095')
        self.assertEqual(self.rows("SELECT * FROM sqlite_master WHERE name='quote_requests'"),[])
        self.session.post.assert_not_called()
        self.client.credentials = fixtures.Credentials('k','s','real')
        with self.assertRaises(ValueError):
            quote_test(self.path,self.client,self.identifier,environment='real',limit=2,api_id='ka10095')
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE collections SET environment='real'")
            db.commit()
        self.session.post.return_value = reply([row('005930'),row('000660')])
        quote_test(self.path,self.client,self.identifier,environment='real',limit=2,api_id='ka10095')
        self.assertEqual(self.session.post.call_args.args[0],'https://api.kiwoom.com/api/dostk/stkinfo')

    def test_interrupt_and_transport_preserve_request_history(self):
        self.session.post.side_effect = requests.Timeout('secret')
        report, code = self.run_batch()
        self.assertEqual((report['status'],code),('error',1))
        request = self.rows('SELECT * FROM quote_requests')[0]
        self.assertIsNotNone(request['started_at'])
        self.assertIsNotNone(request['finished_at'])

    def test_bulk_interrupt_records_times_and_claims(self):
        self.session.post.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_batch()
        request = self.rows('SELECT * FROM quote_requests')[0]
        self.assertEqual(request['status'],'interrupted')
        self.assertIsNotNone(request['started_at'])
        self.assertIsNotNone(request['finished_at'])
        self.assertTrue(all(r['status']=='interrupted' for r in self.rows('SELECT status FROM quote_observations')))
        self.assertEqual(request['finished_at'],self.rows('SELECT request_finished_at FROM quote_observations')[0]['request_finished_at'])
        self.session.post.reset_mock()
        self.assertEqual(self.run_batch()[0]['status'],'skipped')
        self.session.post.assert_not_called()

    def test_auth_failure_has_no_quote_request_and_no_fallback(self):
        self.client.token = None
        self.session.post.return_value = fixtures.response({'return_code':8104})
        self.assertEqual(self.run_batch()[1],1)
        self.assertEqual(self.session.post.call_count,1)
        self.assertTrue(self.session.post.call_args.args[0].endswith('/oauth2/token'))
        self.assertIsNone(self.rows('SELECT started_at FROM quote_requests')[0]['started_at'])

    def test_cli_is_explicit_two_stock_only(self):
        self.session.post.return_value = reply([row('005930'),row('000660')])
        with patch('kiwoom_collector.__main__.load_credentials',return_value=self.client.credentials), \
             patch('kiwoom_collector.__main__.BatchQuoteClient',return_value=self.client), patch('builtins.print'):
            self.assertEqual(main(['quote-batch-test','--collection-id',self.identifier,'--db',str(self.path)]),0)
        with self.assertRaises(SystemExit), patch('sys.stderr'):
            main(['quote-batch-test','--collection-id','x','--limit','1'])

    def legacy_schema(self):
        # Fixture of the exact stage-one columns, not production DB surgery.
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('''CREATE TABLE quote_batches (
                id TEXT PRIMARY KEY,collection_id TEXT NOT NULL REFERENCES collections(id),
                environment TEXT NOT NULL,test_mode INTEGER NOT NULL DEFAULT 1,
                api_id TEXT NOT NULL DEFAULT 'ka10007' CHECK(api_id='ka10007'),
                spec_version TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT,status TEXT NOT NULL,
                UNIQUE(id,collection_id))''')
            db.execute('''CREATE TABLE quote_observations (
                batch_id TEXT NOT NULL,collection_id TEXT NOT NULL,rank INTEGER NOT NULL,requested_code TEXT NOT NULL,
                api_id TEXT NOT NULL DEFAULT 'ka10007' CHECK(api_id='ka10007'),test_mode INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL,request_started_at TEXT,request_finished_at TEXT,http_status INTEGER,
                return_code INTEGER,raw_json TEXT,issues_json TEXT,''' + ','.join(f'{k} TEXT' for k in VALUE_FIELDS) + ''',
                PRIMARY KEY(collection_id,rank,api_id,test_mode),
                FOREIGN KEY(batch_id,collection_id) REFERENCES quote_batches(id,collection_id),
                FOREIGN KEY(collection_id,rank) REFERENCES ranking_records(collection_id,rank))''')
            db.execute("INSERT INTO quote_batches(id,collection_id,environment,spec_version,started_at,status) VALUES('old',?,'mock','v1','old','success')",(self.identifier,))
            db.execute("INSERT INTO quote_observations(batch_id,collection_id,rank,requested_code,status,raw_json,cur_prc) VALUES('old',?,1,'005930','success','{}','-213500')",(self.identifier,))
            db.commit()

    def test_legacy_migration_retains_old_records_and_foreign_keys(self):
        self.legacy_schema()
        old = self.rows('SELECT * FROM quote_observations')[0]
        before = self.original_dump()
        self.session.post.return_value = reply([row('005930'),row('000660')])
        self.assertEqual(self.run_batch()[1],0)
        saved = self.rows("SELECT * FROM quote_observations WHERE api_id='ka10007'")[0]
        self.assertIsNone(saved.pop('normalized_json'))
        self.assertEqual(saved,old)
        self.assertEqual(before,self.original_dump())
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.run_batch()
        self.assertEqual(len(self.rows('SELECT * FROM quote_observations')),3)

    def test_legacy_migration_rollback_restores_original_tables(self):
        self.legacy_schema()
        with closing(sqlite3.connect(self.path)) as db:
            before = list(db.iterdump())
        from kiwoom_collector.quotes import migrate_quotes
        def fail(db):
            migrate_quotes(db)
            raise sqlite3.OperationalError('injected')
        with patch('kiwoom_collector.quotes.migrate_quotes',side_effect=fail), self.assertRaises(sqlite3.OperationalError):
            self.run_batch()
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(before,list(db.iterdump()))
        self.session.post.assert_not_called()

    def test_reported_mock_values_preserved_and_signs_not_guessed(self):
        # User-reported eight values; not a claim of capturing the live response here.
        data = dict(stk_cd='005930',cur_prc='-213500',flu_rt='-4.47',trde_qty='11224535',
                    trde_prica='2440411',open_pric='-221500',high_pric='+226000',low_pric='-212000')
        before = dict(data)
        normal = normalize_quote(data,'ka10007')
        self.assertEqual(data,before)
        self.assertIsNone(normal['current_price_won'])
        self.assertIsNone(normal['high_price_won'])
        self.assertEqual(normal['change_percent'],'-4.47')
        self.assertEqual(normal['volume_shares'],11224535)
        self.assertEqual(normal['turnover_won'],'2440411000000')
        self.assertEqual(normal['price_status'],'unverified_sign_semantics')
        for value in ('',None,'NaN','Infinity','1e4','-1',15):
            self.assertIsNone(normalize_quote({'trde_prica':value},'ka10007')['turnover_won'])
