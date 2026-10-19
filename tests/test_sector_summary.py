from contextlib import closing
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest import TestCase
from unittest.mock import patch

from kiwoom_collector.client import KST, Snapshot
from kiwoom_collector.sectors import SectorManager
from kiwoom_collector.storage import Store
from kiwoom_collector.summary import sector_summary
from kiwoom_collector.__main__ import main


class SectorSummaryTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'stock1.db'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.manager = SectorManager(self.store)
        self.at = datetime(2026, 10, 19, 9, tzinfo=KST)
        self.rows = [
            {'bigd_rank': '1', 'stk_cd': '005930', 'stk_nm': '삼성전자'},
            {'bigd_rank': '3', 'stk_cd': '000660', 'stk_nm': 'SK하이닉스'},
            {'bigd_rank': '8', 'stk_cd': '035420', 'stk_nm': 'NAVER'},
        ]

    def record(self, rows=None, minute=0, environment='mock', test_mode=False):
        at = self.at + timedelta(minutes=minute)
        identifier = self.store.begin(at, environment, '1', test_mode=test_mode)
        self.store.finish(identifier, Snapshot(at, self.rows if rows is None else rows))
        return identifier

    def tag(self):
        self.manager.add('반도체')
        self.manager.add('IT하드웨어')
        self.manager.tag('005930', '반도체')
        self.manager.tag('000660', '반도체')
        self.manager.tag('005930', 'IT하드웨어')

    def test_multisector_unique_counts_unclassified_and_denominator_twenty(self):
        self.record()
        self.tag()
        result = sector_summary(self.path)
        self.assertEqual(result['unique_stock_count'], 3)
        self.assertEqual(result['tagged_unique_stock_count'], 2)
        self.assertEqual(result['unclassified_stock_count'], 1)
        self.assertEqual(result['unclassified_stocks'][0]['stock_code'], '035420')
        semiconductor, hardware = result['sectors']
        self.assertEqual(semiconductor['sector_name'], '반도체')
        self.assertEqual(semiconductor['stock_count'], 2)
        self.assertEqual(semiconductor['best_rank'], 1)
        self.assertEqual(semiconductor['top20_ratio_percent'], 10.0)  # 2/20, not 2/3
        self.assertEqual(semiconductor['label'], '군집 후보')
        self.assertEqual([stock['rank'] for stock in semiconductor['stocks']], [1, 3])
        self.assertEqual([stock['stock_name'] for stock in semiconductor['stocks']], ['삼성전자', 'SK하이닉스'])
        self.assertEqual(hardware['stock_count'], 1)
        self.assertFalse(hardware['cluster_candidate'])
        self.assertEqual(hardware['top20_ratio_percent'], 5.0)
        self.assertEqual(sum(group['stock_count'] for group in result['sectors']), 3)
        self.assertEqual(result['observed_record_count'], 3)

    def test_latest_success_ignores_newer_error_empty_and_other_api(self):
        self.record(minute=0)
        target = self.record([self.rows[1]], minute=1, environment='real')
        self.record([], minute=2)
        error = self.store.begin(self.at + timedelta(minutes=3), 'mock', '1')
        self.store.fail(error, 'simulated failure')
        other = self.record([self.rows[0]], minute=4)
        with self.store.db:
            self.store.db.execute("UPDATE collections SET api_id='other_api' WHERE id=?", (other,))
        result = sector_summary(self.path)
        self.assertEqual(result['collection']['id'], target)
        self.assertEqual(result['collection']['collected_at'], (self.at + timedelta(minutes=1)).isoformat())
        self.assertFalse(result['collection']['is_mock'])
        self.assertFalse(result['collection']['test_mode'])
        self.assertEqual(result['unique_stock_count'], 1)

    def test_test_mode_success_included_but_clearly_labelled(self):
        self.record()
        identifier = self.record(minute=1, test_mode=True)
        run = sector_summary(self.path)['collection']
        self.assertEqual(run['id'], identifier)
        self.assertTrue(run['test_mode'])
        self.assertTrue(run['is_mock'])
        self.assertEqual(run['environment'], 'mock')

    def test_current_tags_with_historical_names_no_write(self):
        identifier = self.record()
        self.tag()
        # Master name changes must not substitute the observed historical name.
        with self.store.db:
            self.store.db.execute("UPDATE stocks SET stock_name='新이름' WHERE stock_code='005930'")
        before = list(self.store.db.iterdump())
        first = sector_summary(self.path)
        self.assertEqual(first['sectors'][0]['stocks'][0]['stock_name'], '삼성전자')
        self.assertEqual(list(self.store.db.iterdump()), before)
        self.manager.untag('000660', '반도체')
        second = sector_summary(self.path)
        semiconductor = next(group for group in second['sectors'] if group['sector_name'] == '반도체')
        self.assertEqual(semiconductor['stock_count'], 1)
        self.assertFalse(semiconductor['cluster_candidate'])
        self.assertEqual(second['collection']['id'], identifier)

    def test_no_success_and_all_untagged(self):
        self.assertIsNone(sector_summary(self.path)['collection'])
        self.record()
        result = sector_summary(self.path)
        self.assertEqual(result['sectors'], [])
        self.assertEqual(result['unclassified_stock_count'], 3)
        self.assertEqual(result['tagged_unique_stock_count'], 0)

    def test_missing_code_is_not_assigned_a_fake_identity(self):
        self.record([self.rows[0], {'bigd_rank': '2', 'stk_nm': '코드 미제공'}])
        result = sector_summary(self.path)
        self.assertEqual(result['unique_stock_count'], 1)
        self.assertEqual(result['unclassified_stock_count'], 1)
        self.assertEqual(result['unidentified_record_count'], 1)
        self.assertIsNone(result['unidentified_records'][0]['stock_code'])

    def test_cli_no_api_credentials_store_initialization_or_data_mutation(self):
        self.record()
        self.tag()
        before = list(self.store.db.iterdump())
        with patch('kiwoom_collector.__main__.Store', side_effect=AssertionError('must not migrate')), patch('kiwoom_collector.__main__.load_credentials', side_effect=AssertionError('no credentials')), patch('kiwoom_collector.client.requests.Session', side_effect=AssertionError('no API')), patch('builtins.print') as output:
            self.assertEqual(main(['sector', 'summary', '--db', str(self.path)]), 0)
        result = json.loads(output.call_args.args[0])
        self.assertEqual(result['sectors'][0]['label'], '군집 후보')
        self.assertEqual(list(self.store.db.iterdump()), before)

    def test_read_only_connection_rejects_write_and_closes_after_failure(self):
        self.record()
        connections = []
        real_connect = sqlite3.connect
        def track(*args, **kwargs):
            self.assertIn('?mode=ro', args[0])
            self.assertTrue(kwargs['uri'])
            db = real_connect(*args, **kwargs)
            connections.append(db)
            return db
        def attempt_write(db, path):
            db.execute("UPDATE collections SET status='error'")
        before = list(self.store.db.iterdump())
        with patch('kiwoom_collector.summary.sqlite3.connect', side_effect=track), patch('kiwoom_collector.summary._summarize', side_effect=attempt_write):
            with self.assertRaises(sqlite3.OperationalError):
                sector_summary(self.path)
        self.assertEqual(list(self.store.db.iterdump()), before)
        for db in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                db.execute('SELECT 1')

    def test_missing_db_and_old_schema_are_not_created_or_migrated(self):
        missing = Path(self.temp.name) / 'missing' / 'missing.db'
        with self.assertRaises(ValueError):
            sector_summary(missing)
        self.assertFalse(missing.parent.exists())
        old = Path(self.temp.name) / 'legacy.db'
        with closing(sqlite3.connect(old)) as db, db:
            db.execute('CREATE TABLE legacy(value TEXT)')
            before = list(db.iterdump())
        with self.assertRaises(ValueError):
            sector_summary(old)
        with closing(sqlite3.connect(old)) as db:
            self.assertEqual(list(db.iterdump()), before)
