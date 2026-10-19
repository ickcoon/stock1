"""Synthetic regressions; these fixtures are not claimed to be actual server responses."""
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest import TestCase
from unittest.mock import Mock, patch

from kiwoom_collector.client import KST, KiwoomClient
from kiwoom_collector.collector import test_now
from kiwoom_collector.config import Credentials
from kiwoom_collector.diagnostics import response_shape
from kiwoom_collector.storage import Store
from kiwoom_collector.__main__ import main


class DiagnosticsTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'stock1.db'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.at = datetime(2026, 10, 19, 18, 0, tzinfo=KST)
        self.session_patch = patch('kiwoom_collector.client.requests.Session')
        self.session = self.session_patch.start().return_value
        self.addCleanup(self.session_patch.stop)
        self.sleep_patch = patch('kiwoom_collector.client.time.sleep')
        self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)
        self.client = KiwoomClient(Credentials('secret-app-key', 'secret-secret-key'))
        self.addCleanup(self.client.close)

    def prepare(self, body, status=200):
        auth = Mock(status_code=200, headers={})
        auth.json.return_value = {'token': 'secret-access-token', 'token_type': 'bearer', 'expires_dt': '20990101000000'}
        page = Mock(status_code=status, headers={})
        page.json.return_value = body
        self.session.post.side_effect = [auth, page]

    def run_attempt(self):
        with self.assertLogs('kiwoom_collector.collector', level='INFO') as logs:
            result = test_now(self.client, self.store, 'mock', now=lambda: self.at)
        row = dict(self.store.db.execute('SELECT * FROM collections').fetchone())
        return result, row, '\n'.join(logs.output)

    def assert_no_secrets(self, row, logs):
        combined = json.dumps(row) + logs
        for secret in ('secret-app-key', 'secret-secret-key', 'secret-access-token', 'secret-row-value'):
            self.assertNotIn(secret, combined)

    def test_once_accepts_numeric_nonrank_field_but_storage_reports_exact_field(self):
        # Models the difference in existing once vs persistence, NOT a confirmed real response.
        body = {'return_code': 0, 'item_inq_rank': [{'bigd_rank': '1', 'stk_cd': '039490', 'base_comp_chgr': 0.57}]}
        self.prepare(body)
        snapshot = self.client.fetch_once(single_request=True)
        self.assertEqual(snapshot.records[0]['base_comp_chgr'], 0.57)
        # Re-run via the persistence path with a fresh session/token.
        self.client.token = None
        self.prepare(body)
        result, row, logs = self.run_attempt()
        self.assertEqual((result, row['status'], row['error_stage']), (1, 'error', 'response_validation'))
        self.assertIn('selected[0].base_comp_chgr', row['error'])
        self.assertIn('expected=str actual=float', row['error'])
        self.assertEqual(json.loads(row['diagnostic_json'])['source_stage'], 'db_store')
        self.assertEqual(self.store.status()['records'], 0)

    def test_null_field_diagnostic_redacts_all_values_and_unknown_fields(self):
        self.prepare({'return_code': 0, 'return_msg': 'secret-access-token',
                      'token': 'secret-access-token', 'secret-app-key': 'secret-secret-key',
                      'item_inq_rank': [{'bigd_rank': '1', 'stk_nm': 'secret-row-value', 'dt': None}]})
        result, row, logs = self.run_attempt()
        self.assertEqual(result, 1)
        self.assertIn('selected[0].dt', row['error'])
        self.assertIn('actual=null', row['error'])
        diag = json.loads(row['diagnostic_json'])
        self.assertEqual(diag['http_status'], 200)
        self.assertEqual(diag['field_types']['dt'], {'null': 1})
        self.assert_no_secrets(row, logs)

    def test_duplicate_stock_is_database_failure_with_rollback(self):
        self.prepare({'return_code': 0, 'item_inq_rank': [
            {'bigd_rank': '1', 'stk_cd': '039490'}, {'bigd_rank': '2', 'stk_cd': '039490'}]})
        result, row, logs = self.run_attempt()
        self.assertEqual((result, row['error_stage']), (1, 'db_store'))
        self.assertIn('sqlite_errorcode=2067', row['error'])
        self.assertIn('collection_id+stk_cd 중복', row['error'])
        self.assertEqual(self.store.status()['records'], 0)
        self.assert_no_secrets(row, logs)

    def test_api_error_remains_api_error(self):
        self.prepare({'return_code': 999, 'return_msg': 'secret-access-token'}, status=403)
        result, row, logs = self.run_attempt()
        self.assertEqual((result, row['error_stage']), (1, 'api_request'))
        self.assertIn('403', row['error'])
        self.assertEqual(json.loads(row['diagnostic_json'])['http_status'], 403)
        self.assert_no_secrets(row, logs)

    def test_unexpected_list_type_is_validation_not_database_failure(self):
        self.prepare({'return_code': 0, 'item_inq_rank': None})
        result, row, logs = self.run_attempt()
        self.assertEqual((result, row['error_stage']), (1, 'response_validation'))
        self.assertEqual(json.loads(row['diagnostic_json'])['item_inq_rank_type'], 'null')
        self.assertIn('list[dict]', row['error'])

    def test_empty_list_and_missing_optional_list_remain_empty(self):
        for body in ({'return_code': 0, 'item_inq_rank': []}, {'return_code': 0}):
            with self.subTest(body=body):
                self.client.token = None
                self.prepare(body)
                self.assertEqual(test_now(self.client, self.store, 'mock', now=lambda: self.at), 0)
                self.assertEqual(self.store.latest()[0]['status'], 'empty')
                self.at = self.at.replace(minute=1)

    def test_database_write_failure_has_safe_message_and_history(self):
        self.prepare({'return_code': 0, 'item_inq_rank': []})
        with patch.object(self.store, 'finish', side_effect=sqlite3.OperationalError('secret-row-value')):
            result, row, logs = self.run_attempt()
        self.assertEqual((result, row['error_stage']), (1, 'db_store'))
        self.assert_no_secrets(row, logs)

    def test_status_displays_persisted_failure_and_safe_shape(self):
        self.prepare({'return_code': 0, 'item_inq_rank': [{'bigd_rank': '1', 'tm': None}]})
        self.run_attempt()
        with patch('builtins.print') as output:
            self.assertEqual(main(['status', '--db', str(self.path)]), 0)
        text = str(output.call_args_list)
        self.assertIn('response_validation', text)
        self.assertIn('selected[0].tm', text)
        self.assertNotIn('secret-access-token', text)

    def test_shape_is_allowlisted_even_for_hostile_key_names(self):
        diag = response_shape({'secret-app-key': 'secret-secret-key', 'item_inq_rank': [{'secret-access-token': 'secret-row-value'}]})
        for secret in ('secret-app-key', 'secret-secret-key', 'secret-access-token', 'secret-row-value'):
            self.assertNotIn(secret, json.dumps(diag))

    def test_legacy_database_adds_diagnostics_and_preserves_failure(self):
        path = Path(self.temp.name) / 'legacy.db'
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TABLE collections (
                id TEXT PRIMARY KEY, collected_at TEXT NOT NULL, environment TEXT NOT NULL,
                api_id TEXT NOT NULL DEFAULT 'ka00198', qry_tp TEXT NOT NULL,
                status TEXT NOT NULL, record_count INTEGER NOT NULL DEFAULT 0,
                error TEXT, response_json TEXT, test_mode INTEGER NOT NULL DEFAULT 0,
                UNIQUE(collected_at,environment,api_id,qry_tp))""")
            db.execute("INSERT INTO collections(id,collected_at,environment,qry_tp,status,error) VALUES('legacy','2026-10-19T18:00:00+09:00','mock','1','error','old generic validation error')")
        migrated = Store(path)
        try:
            failure = migrated.last_failure()
            self.assertEqual(failure['id'], 'legacy')
            self.assertEqual(failure['error'], 'old generic validation error')
            self.assertIsNone(failure['error_stage'])
            self.assertIsNone(failure['diagnostic_json'])
        finally:
            migrated.close()
