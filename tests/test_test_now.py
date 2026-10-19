from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
from unittest import TestCase
from unittest.mock import Mock, patch

from kiwoom_collector.client import KST, KiwoomClient, KiwoomError, Snapshot
from kiwoom_collector.config import Credentials
from kiwoom_collector.collector import test_now
from kiwoom_collector.storage import Store
from kiwoom_collector.__main__ import main


class TestNowTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'stock1.db'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.at = datetime(2026, 10, 24, 18, 0, tzinfo=KST)  # Saturday after hours

    def test_after_hours_success_and_empty(self):
        for minute, rows in ((0, [{'bigd_rank': '1', 'stk_nm': '테스트'}]), (1, [])):
            client = Mock()
            client.fetch_once.return_value = Snapshot(self.at, rows)
            self.assertEqual(test_now(client, self.store, 'mock', now=lambda: self.at.replace(minute=minute)), 0)
            client.fetch_once.assert_called_once_with('1', single_request=True)
            run, _ = self.store.latest()
            self.assertEqual(run['test_mode'], 1)
            self.assertEqual(run['status'], 'success' if rows else 'empty')

    def test_error_recorded_and_no_retry(self):
        client = Mock()
        client.fetch_once.side_effect = KiwoomError('HTTP 403: API 요청 실패')
        self.assertEqual(test_now(client, self.store, 'mock', now=lambda: self.at), 1)
        client.fetch_once.assert_called_once()
        run = self.store.db.execute('SELECT * FROM collections').fetchone()
        self.assertEqual((run['status'], run['test_mode'], run['record_count']), ('error', 1, 0))
        self.assertIn('403', run['error'])

    def test_actual_http_single_request_contract(self):
        for status, body, headers in (
            (200, {'return_code': 0, 'item_inq_rank': []}, {}),
            (429, {}, {}), (401, {}, {}), (500, {}, {}),
            (200, {'return_code': 8005}, {}),
            (200, {'return_code': 0, 'item_inq_rank': []}, {'cont-yn': 'Y', 'next-key': 'next'}),
        ):
            with self.subTest(status=status, body=body), patch('kiwoom_collector.client.requests.Session') as factory, patch('kiwoom_collector.client.time.sleep'):
                client = KiwoomClient(Credentials('test-key', 'test-secret'))
                auth = Mock(status_code=200, headers={})
                auth.json.return_value = {'token': 'test-token', 'token_type': 'bearer', 'expires_dt': '20990101000000'}
                page = Mock(status_code=status, headers=headers)
                page.json.return_value = body
                factory.return_value.post.side_effect = [auth, page]
                try:
                    if status != 200 or headers or body.get('return_code'):
                        with self.assertRaises(KiwoomError):
                            client.fetch_once(single_request=True)
                    else:
                        self.assertEqual(client.fetch_once(single_request=True).records, [])
                finally:
                    calls = factory.return_value.post.call_args_list
                    self.assertEqual(len(calls), 2)  # one auth + exactly one ranking request
                    self.assertTrue(calls[1].args[0].endswith('/api/dostk/stkinfo'))
                    client.close()

    def test_existing_database_migrates_without_losing_history(self):
        old_path = Path(self.temp.name) / 'old.db'
        # Reproduce the pre-test-mode schema, including the original uniqueness constraint.
        source = self.store.db.execute("SELECT sql FROM sqlite_master WHERE name='collections'").fetchone()[0]
        # Current migration added test_mode as the final column before UNIQUE in SQLite's SQL text.
        import re
        source = re.sub(r',?\s*test_mode INTEGER NOT NULL DEFAULT 0 CHECK\(test_mode IN \(0,1\)\)', '', source)
        with sqlite3.connect(old_path) as old:
            old.execute(source)
            old.execute("INSERT INTO collections(id,collected_at,environment,qry_tp,status) VALUES('old','2026-10-19T09:00:00+09:00','mock','1','empty')")
        migrated = Store(old_path)
        try:
            row = migrated.db.execute("SELECT * FROM collections WHERE id='old'").fetchone()
            self.assertEqual((row['status'], row['test_mode']), ('empty', 0))
            self.assertEqual(migrated.status()['attempts'], 1)
        finally:
            migrated.close()

    def test_cli_test_now_routes_once_and_closes_resources(self):
        with patch('kiwoom_collector.__main__.load_credentials', return_value=Credentials('x', 'y')), patch('kiwoom_collector.__main__.KiwoomClient') as factory, patch('kiwoom_collector.__main__.collect') as scheduler, patch('builtins.print'):
            factory.return_value.fetch_once.return_value = Snapshot(self.at, [])
            self.assertEqual(main(['collect', '--test-now', '--db', str(self.path)]), 0)
            scheduler.assert_not_called()
            factory.return_value.fetch_once.assert_called_once_with('1', single_request=True)
            factory.return_value.close.assert_called_once()
