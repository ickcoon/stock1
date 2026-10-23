"""Offline ka10007 contracts and additive storage on temporary databases only."""
from contextlib import closing
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, Mock

import requests
from clock_helpers import request_clock

from kiwoom_collector.client import KST, Snapshot, Token
from kiwoom_collector.config import Credentials
from kiwoom_collector.quotes import QuoteClient, VALUE_FIELDS, quote_test, migrate_quotes
from kiwoom_collector.storage import Store
from kiwoom_collector.__main__ import main


def body(code='005930', **extra):
    return dict(return_code=0, stk_cd=code, stk_nm='삼성전자', cur_prc='+070000',
                flu_rt='-1.25', trde_qty='0', trde_prica='1234.56', open_pric='+71000',
                high_pric='+72000', low_pric='-69000', date='20261022', tm='093000', **extra)


def response(data=None, status=200, headers=None):
    value = Mock(status_code=status, headers=headers or {})
    value.json.return_value = body() if data is None else data
    return value


class QuoteFixture(unittest.TestCase):
    def setUp(self):
        self.patch = patch('kiwoom_collector.client.requests.Session')
        self.session = self.patch.start().return_value
        self.addCleanup(self.patch.stop)
        self.request_clock = self.enterContext(request_clock())
        self.client = QuoteClient(Credentials('key', 'secret'))
        self.client.token = Token('token', datetime.now(KST) + timedelta(hours=1))
        self.addCleanup(self.client.close)


class QuoteClientTests(QuoteFixture):
    def test_exact_contract_raw_units_and_times(self):
        self.session.post.return_value = response(body(token='must-not-store', return_msg='secret'))
        quote = self.client.fetch_quote('005930')
        self.assertEqual(quote.status, 'success')
        call = self.session.post.call_args
        self.assertEqual(call.args, ('https://mockapi.kiwoom.com/api/dostk/mrkcond',))
        self.assertEqual(call.kwargs['json'], {'stk_cd': '005930'})
        self.assertEqual(call.kwargs['headers']['api-id'], 'ka10007')
        self.assertEqual(call.kwargs['headers']['authorization'], 'Bearer token')
        self.assertFalse(call.kwargs['allow_redirects'])
        self.assertNotIn('verify', call.kwargs)
        self.assertEqual(quote.raw['cur_prc'], '+070000')
        self.assertEqual(quote.raw['trde_prica'], '1234.56')
        self.assertEqual(quote.raw['trde_qty'], '0')
        self.assertNotIn('token', quote.raw)
        self.assertNotIn('return_msg', quote.raw)
        self.assertLessEqual(quote.request_started_at, quote.request_finished_at)
        self.assertTrue(quote.request_started_at.endswith('+09:00'))

    def test_real_host_is_explicit(self):
        self.client.credentials = Credentials('real-key', 'real-secret', 'real')
        self.session.post.return_value = response()
        self.client.fetch_quote('005930')
        self.assertEqual(self.session.post.call_args.args[0], 'https://api.kiwoom.com/api/dostk/mrkcond')

    def test_http_api_network_json_and_continuation_fail_without_retry(self):
        for reply in (response(status=429), response(status=503), response({'return_code': 1700}),
                      response({'return_code': 8104}), response([]), response({}),
                      response({'return_code': True}), response(headers={'cont-yn': 'Y'})):
            with self.subTest(reply=reply):
                self.session.post.reset_mock()
                self.session.post.return_value = reply
                quote = self.client.fetch_quote('005930')
                self.assertEqual(quote.status, 'error')
                self.assertEqual(self.session.post.call_count, 1)
                self.assertEqual(quote.raw, {})
        for exc in (requests.Timeout('secret'), requests.exceptions.SSLError('secret')):
            self.session.post.side_effect = exc
            quote = self.client.fetch_quote('005930')
            self.assertEqual(quote.issues, ['transport_failed'])
            self.assertIsNotNone(quote.request_finished_at)
        self.session.post.side_effect = None
        reply = response()
        reply.json.side_effect = ValueError('secret')
        self.session.post.return_value = reply
        self.assertEqual(self.client.fetch_quote('005930').issues, ['invalid_json'])

    def test_missing_fields_types_empty_and_invalid_time(self):
        for field in VALUE_FIELDS:
            data = body()
            del data[field]
            self.session.post.return_value = response(data)
            result = self.client.fetch_quote('005930')
            self.assertEqual(result.status, 'partial')
            self.assertNotIn(field, result.raw)
            self.assertIn('missing:' + field, result.issues)
        data = body()
        data.update(cur_prc=70000, date='20260230', tm='99', trde_qty='')
        self.session.post.return_value = response(data)
        result = self.client.fetch_quote('005930')
        self.assertEqual(result.status, 'partial')
        self.assertNotIn('cur_prc', result.raw)
        self.assertIn('invalid_format:date', result.issues)
        self.assertIn('invalid_format:tm', result.issues)
        self.assertIn('missing:trde_qty', result.issues)
        self.session.post.return_value = response({'return_code': 0})
        self.assertEqual(self.client.fetch_quote('005930').status, 'error')

    def test_mismatched_code_and_invalid_request(self):
        self.session.post.return_value = response(body('000660'))
        result = self.client.fetch_quote('005930')
        self.assertEqual((result.status, result.raw), ('error', {}))
        for code in ('', 'A005930', '005930|000660', '００５９３０', None):
            with self.assertRaises(ValueError):
                self.client.fetch_quote(code)

    def test_auth_failure_does_not_claim_quote_http_time(self):
        self.client.token = None
        self.session.post.return_value = response({'return_code': 8104})
        result = self.client.fetch_quote('005930')
        self.assertEqual(result.issues, ['authentication_failed'])
        self.assertIsNone(result.request_started_at)
        self.assertIsNone(result.request_finished_at)
        self.assertEqual(self.session.post.call_count, 1)
        self.assertTrue(self.session.post.call_args.args[0].endswith('/oauth2/token'))


class QuoteStorageTests(QuoteFixture):
    # Inherit the HTTP guard; every fixture has real SQLite but fake HTTP.
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'stock1.db'
        at = datetime(2026, 10, 22, 9, 20, tzinfo=KST)
        with closing(Store(self.path)) as store:
            self.identifier = store.begin(at, 'mock', '1', scheduled_at=at)
            store.finish(self.identifier, Snapshot(at, [
                {'bigd_rank': '1', 'stk_cd': '005930', 'stk_nm': '삼성전자'},
                {'bigd_rank': '2', 'stk_cd': '000660', 'stk_nm': 'SK하이닉스'}]))
            # A real theme fixture, protected by original-table dump comparison.
            store.db.execute("INSERT INTO sectors(sector_name,created_at) VALUES('반도체',?)", (at.isoformat(),))
            store.db.execute("INSERT INTO stock_sectors(stock_code,sector_id,source,created_at,updated_at) VALUES('005930',1,'manual',?,?)",
                             (at.isoformat(), at.isoformat()))
            store.db.commit()

    def rows(self, sql, args=()):
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(sql, args).fetchall()]

    def original_dump(self):
        with closing(sqlite3.connect(self.path)) as db:
            return [line for line in db.iterdump() if 'quote_' not in line]

    def test_additive_migration_partial_failure_and_duplicate_execution(self):
        before = self.original_dump()
        self.session.post.side_effect = [response(), response(status=500)]
        report, code = quote_test(self.path, self.client, self.identifier, limit=2)
        self.assertEqual((report['status'], code), ('partial', 1))
        rows = self.rows('SELECT * FROM quote_observations ORDER BY rank')
        self.assertEqual([r['status'] for r in rows], ['success', 'error'])
        self.assertEqual(rows[0]['trde_qty'], '0')
        self.assertEqual(rows[0]['cur_prc'], '+070000')
        self.assertEqual(json.loads(rows[0]['raw_json'])['date'], '20261022')
        self.assertEqual(before, self.original_dump())
        self.session.post.reset_mock()
        duplicate, code = quote_test(self.path, self.client, self.identifier, limit=2)
        self.assertEqual((duplicate['status'], duplicate['skipped'], code), ('skipped', 2, 0))
        self.session.post.assert_not_called()
        self.assertEqual(len(self.rows('SELECT * FROM quote_observations')), 2)
        self.assertEqual(before, self.original_dump())

    def test_limit_expansion_only_calls_new_target(self):
        self.session.post.return_value = response()
        quote_test(self.path, self.client, self.identifier)
        self.session.post.return_value = response(body('000660'))
        result, code = quote_test(self.path, self.client, self.identifier, limit=2)
        self.assertEqual((result['attempted'], result['skipped'], code), (1, 1, 0))
        self.assertEqual(self.session.post.call_count, 2)

    def test_environment_parent_and_limit_rejected_before_migration_or_network(self):
        before = self.original_dump()
        for identifier, environment, limit in ((self.identifier, 'real', 1), ('missing', 'mock', 1),
                                               (self.identifier, 'mock', 3)):
            with self.assertRaises(ValueError):
                quote_test(self.path, self.client, identifier, environment=environment, limit=limit)
        self.client.credentials = Credentials('k', 's', 'real')
        with self.assertRaises(ValueError):
            quote_test(self.path, self.client, self.identifier, environment='real')
        self.assertEqual(before, self.original_dump())
        self.assertEqual(self.rows("SELECT name FROM sqlite_master WHERE name LIKE 'quote_%'"), [])
        self.session.post.assert_not_called()
        missing = self.path.parent / 'missing.db'
        with self.assertRaises(sqlite3.OperationalError):
            quote_test(missing, self.client, self.identifier, environment='real')
        self.assertFalse(missing.exists())

    def test_real_collection_and_test_flag_independent_of_parent(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE collections SET environment='real',test_mode=0 WHERE id=?", (self.identifier,))
            db.commit()
        self.client.credentials = Credentials('k', 's', 'real')
        self.session.post.return_value = response()
        quote_test(self.path, self.client, self.identifier, environment='real')
        self.assertEqual(self.rows('SELECT environment,test_mode FROM quote_batches')[0],
                         {'environment': 'real', 'test_mode': 1})
        self.assertEqual(self.rows('SELECT test_mode,status FROM collections')[0],
                         {'test_mode': 0, 'status': 'success'})

    def test_interruption_records_claims_and_never_replays(self):
        self.session.post.side_effect = [response(), KeyboardInterrupt()]
        with self.assertRaises(KeyboardInterrupt):
            quote_test(self.path, self.client, self.identifier, limit=2)
        self.assertEqual([r['status'] for r in self.rows('SELECT status FROM quote_observations ORDER BY rank')],
                         ['success', 'interrupted'])
        self.assertEqual(self.rows('SELECT status FROM quote_batches')[0]['status'], 'interrupted')
        interrupted = self.rows('SELECT request_started_at,request_finished_at FROM quote_observations WHERE rank=2')[0]
        self.assertIsNotNone(interrupted['request_started_at'])
        self.assertIsNotNone(interrupted['request_finished_at'])
        self.session.post.reset_mock()
        quote_test(self.path, self.client, self.identifier, limit=2)
        self.session.post.assert_not_called()

    def test_migration_failure_rolls_back_all_new_schema(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('BEGIN IMMEDIATE')
            def deny_trigger(action, *args):
                return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_TRIGGER else sqlite3.SQLITE_OK
            db.set_authorizer(deny_trigger)
            with self.assertRaises(sqlite3.DatabaseError):
                migrate_quotes(db)
            db.rollback()
            db.set_authorizer(None)
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'quote_%'").fetchall(), [])

    def test_concurrent_claim_skips_http_and_constraints_enforce_parent(self):
        def during_request(*args, **kwargs):
            duplicate, code = quote_test(self.path, self.client, self.identifier)
            self.assertEqual((duplicate['status'], code), ('skipped', 0))
            return response()
        self.session.post.side_effect = during_request
        quote_test(self.path, self.client, self.identifier)
        self.assertEqual(self.session.post.call_count, 1)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA foreign_keys=ON')
            batch_id = db.execute("SELECT id FROM quote_batches WHERE status='success'").fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError), db:
                db.execute('''INSERT INTO quote_observations(batch_id,collection_id,rank,requested_code,status)
                    VALUES(?,?,20,'999999','running')''', (batch_id, self.identifier))
            with self.assertRaises(sqlite3.IntegrityError), db:
                db.execute('''INSERT INTO quote_batches(id,collection_id,environment,spec_version,started_at,status)
                    VALUES('bad',?,'real','v','t','running')''', (self.identifier,))

    def test_cli_routes_independently_and_closes_client(self):
        self.session.post.return_value = response()
        with patch('kiwoom_collector.__main__.load_credentials', return_value=self.client.credentials), \
             patch('kiwoom_collector.__main__.QuoteClient', return_value=self.client), \
             patch('kiwoom_collector.__main__.Store', side_effect=AssertionError('no common migration')), \
             patch('builtins.print'):
            self.assertEqual(main(['quote-test', '--db', str(self.path), '--collection-id', self.identifier]), 0)
        self.session.close.assert_called()
        for argv in (['quote-test'], ['quote-test', '--collection-id', 'x', '--limit', '3'],
                     ['collect', '--collection-id', 'x'], ['quote-test', '--collection-id', 'x', '--schedule']):
            with self.assertRaises(SystemExit), patch('sys.stderr'), patch('kiwoom_collector.__main__.load_credentials',
                                                     side_effect=AssertionError('must validate first')):
                main(argv)


if __name__ == '__main__':
    unittest.main()
