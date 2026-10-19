from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest import TestCase
from unittest.mock import patch

from kiwoom_collector.client import KST, Snapshot
from kiwoom_collector.storage import Store
from kiwoom_collector.sectors import SectorManager
from kiwoom_collector.__main__ import main


class SectorTests(TestCase):
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
            {'bigd_rank': '2', 'stk_cd': '000660', 'stk_nm': 'SK하이닉스'},
            {'bigd_rank': '3', 'stk_cd': '035420', 'stk_nm': 'NAVER'},
        ]
        self.record(self.rows)

    def record(self, rows, offset=0):
        at = self.at + timedelta(minutes=offset)
        identifier = self.store.begin(at, 'mock', '1')
        self.store.finish(identifier, Snapshot(at, rows))
        return identifier

    def csv(self, text, encoding='utf-8-sig'):
        path = Path(self.temp.name) / 'tags.csv'
        path.write_text(text, encoding=encoding)
        return path

    def test_collected_stocks_keep_leading_zero_and_update_name_only(self):
        self.assertEqual(self.manager.show('005930')['stock_name'], '삼성전자')
        self.manager.add('반도체')
        self.manager.tag('005930', '반도체')
        renamed = dict(self.rows[0], stk_nm='변경된 이름')
        self.record([renamed], offset=5)
        stock = self.manager.show('005930')
        self.assertEqual(stock['stock_name'], '변경된 이름')
        self.assertEqual(stock['stock_code'], '005930')
        self.assertEqual(len(stock['sectors']), 1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM stocks').fetchone()[0], 3)
        self.assertEqual(self.store.status()['records'], 4)
        old_name = self.store.db.execute('SELECT stk_nm FROM ranking_records ORDER BY rowid LIMIT 1').fetchone()[0]
        self.assertEqual(old_name, '삼성전자')

    def test_multiple_tags_idempotent_source_update_and_untag_audit(self):
        first = self.manager.add('반도체')
        self.assertEqual(first, self.manager.add('반도체'))
        self.manager.add('AI')
        self.assertEqual(self.manager.tag('005930', '반도체'), 'added')
        self.assertEqual(self.manager.tag('005930', '반도체'), 'unchanged')
        self.manager.tag('005930', 'AI')
        with patch('kiwoom_collector.sectors.timestamp', return_value='2026-10-20T09:00:00+09:00'):
            self.assertEqual(self.manager.tag('005930', '반도체', '인포스탁 참고·수동 검토'), 'updated')
        tags = self.manager.show('005930')['sectors']
        self.assertEqual(len(tags), 2)
        semiconductor = next(row for row in tags if row['sector_name'] == '반도체')
        self.assertEqual(semiconductor['updated_at'], '2026-10-20T09:00:00+09:00')
        self.assertEqual(semiconductor['source'], '인포스탁 참고·수동 검토')
        self.assertTrue(self.manager.untag('005930', '반도체', '수동 정정'))
        self.assertFalse(self.manager.untag('005930', '반도체'))
        event = self.store.db.execute('SELECT * FROM stock_sector_events ORDER BY event_id DESC LIMIT 1').fetchone()
        self.assertEqual((event['action'], event['source']), ('untag', '수동 정정'))
        self.assertEqual(len(self.manager.show('005930')['sectors']), 1)
        self.assertEqual(len(self.manager.untagged()), 2)
        self.assertTrue(any(row['sector_name'] == '반도체' and row['stock_code'] is None for row in self.manager.list()))

    def test_validation_and_foreign_keys(self):
        self.manager.add('반도체')
        for code in ('5930', '００５９３０', '1234567', 'A05930', 5930):
            with self.subTest(code=code), self.assertRaises(ValueError):
                self.manager.tag(code, '반도체')
        with self.assertRaises(ValueError):
            self.manager.tag('999999', '반도체')
        with self.assertRaises(ValueError):
            self.manager.tag('005930', '없는 섹터')
        with self.assertRaises(sqlite3.IntegrityError), self.store.db:
            self.store.db.execute("INSERT INTO stock_sectors VALUES('999999',1,'수동','x','x')")
        with self.assertRaises(sqlite3.IntegrityError), self.store.db:
            self.store.db.execute("INSERT INTO stock_sectors VALUES('005930',99999,'수동','x','x')")
        self.assertEqual(self.store.db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_csv_dry_run_then_apply_with_multi_sector_and_source(self):
        path = self.csv('stock_code,sector_name\n005930,반도체\n005930,AI\n000660,반도체\n')
        report = self.manager.import_csv(path, source='인포스탁 참고', dry_run=True)
        self.assertFalse(report['applied'])
        self.assertEqual((report['valid_rows'], report['added']), (3, 3))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM sectors').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM stock_sector_events').fetchone()[0], 0)
        result = self.manager.import_csv(path, source='인포스탁 참고')
        self.assertTrue(result['applied'])
        self.assertEqual(len(self.manager.show('005930')['sectors']), 2)
        self.assertEqual(self.manager.show('000660')['sectors'][0]['source'], '인포스탁 참고')
        repeated = self.manager.import_csv(path, source='인포스탁 참고')
        self.assertEqual(repeated['unchanged'], 3)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM stock_sector_events').fetchone()[0], 3)

    def test_csv_invalid_unknown_duplicate_rows_block_entire_import(self):
        path = self.csv('stock_code,sector_name\n005930,반도체\n005930,반도체\n999999,AI\n5930,반도체\n')
        for dry_run in (True, False):
            report = self.manager.import_csv(path, dry_run=dry_run)
            self.assertEqual(len(report['errors']), 3)
            self.assertFalse(report['applied'])
            self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM sectors').fetchone()[0], 0)
            self.assertEqual(self.manager.show('005930')['sectors'], [])

    def test_csv_invalid_columns_and_encoding(self):
        for content in ('wrong,sector_name\n005930,반도체\n', 'stock_code,sector_name\n005930,반도체\n'):
            if content.startswith('wrong'):
                with self.assertRaises(ValueError):
                    self.manager.import_csv(self.csv(content))
            else:
                with self.assertRaises(ValueError):
                    self.manager.import_csv(self.csv(content, encoding='cp949'))
        report = self.manager.import_csv(self.csv('stock_code,sector_name\n005930,반도체,extra\n000660\n'))
        self.assertEqual(len(report['errors']), 2)
        self.assertFalse(report['applied'])

    def test_csv_runtime_failure_rolls_back_new_sectors_links_events(self):
        path = self.csv('stock_code,sector_name\n005930,반도체\n000660,반도체\n')
        original = self.manager._tag
        calls = [0]
        def failing_tag(*args):
            calls[0] += 1
            if calls[0] == 2:
                raise sqlite3.IntegrityError('injected failure')
            return original(*args)
        with patch.object(self.manager, '_tag', side_effect=failing_tag), self.assertRaises(sqlite3.IntegrityError):
            self.manager.import_csv(path)
        for table in ('sectors', 'stock_sectors', 'stock_sector_events'):
            self.assertEqual(self.store.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)

    def test_collection_rollback_also_rolls_back_master_name(self):
        identifier = self.store.begin(self.at + timedelta(minutes=5), 'mock', '1')
        rows = [dict(self.rows[0], stk_nm='잘못된 이름'), dict(self.rows[1], bigd_rank='1')]
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.finish(identifier, Snapshot(self.at, rows))
        self.assertEqual(self.manager.show('005930')['stock_name'], '삼성전자')
        self.assertEqual(self.store.status()['records'], 3)

    def test_migration_backfills_old_history_and_is_repeatable(self):
        # Remove only new schema from a disposable fixture to model the existing database.
        self.store.close()
        with sqlite3.connect(self.path) as db:
            for table in ('stock_sector_events', 'stock_sectors', 'sectors', 'stocks', 'schema_migrations'):
                db.execute(f'DROP TABLE {table}')
        migrated = Store(self.path)
        try:
            self.assertEqual(SectorManager(migrated).show('005930')['stock_name'], '삼성전자')
            self.assertEqual(migrated.status()['records'], 3)
            self.assertEqual(migrated.status()['attempts'], 1)
            SectorManager(migrated).add('반도체')
            SectorManager(migrated).tag('005930', '반도체')
        finally:
            migrated.close()
        reopened = Store(self.path)
        try:
            self.assertEqual(len(SectorManager(reopened).show('005930')['sectors']), 1)
            self.assertEqual(reopened.status()['records'], 3)
            self.assertEqual(reopened.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        finally:
            reopened.close()

    def test_cli_sector_commands_do_not_load_credentials_or_request_api(self):
        commands = [(['add', '반도체'], 0), (['tag', '005930', '반도체', '--source', '수동 검토'], 0),
                    (['show', '005930'], 0), (['list'], 0), (['untagged'], 0),
                    (['untag', '005930', '반도체'], 0), (['tag', '999999', '반도체'], 1)]
        with patch('kiwoom_collector.__main__.load_credentials', side_effect=AssertionError('no credentials')), patch('kiwoom_collector.client.requests.Session', side_effect=AssertionError('no API')), patch('builtins.print'):
            for args, result in commands:
                self.assertEqual(main(['sector', *args, '--db', str(self.path)]), result)
            path = self.csv('stock_code,sector_name\n005930,반도체\n')
            self.assertEqual(main(['sector', 'import', str(path), '--dry-run', '--db', str(self.path)]), 0)
        self.assertEqual(self.manager.show('005930')['sectors'], [])

    def test_csv_source_update_and_dry_run_do_not_change_prior_provenance(self):
        path = self.csv('stock_code,sector_name\n005930,반도체\n')
        self.manager.import_csv(path, source='수동')
        before = self.manager.show('005930')['sectors'][0]
        report = self.manager.import_csv(path, source='인포스탁 참고', dry_run=True)
        self.assertEqual(report['updated'], 1)
        self.assertEqual(self.manager.show('005930')['sectors'][0], before)
        report = self.manager.import_csv(path, source='인포스탁 참고')
        self.assertEqual(report['updated'], 1)
        after = self.manager.show('005930')['sectors'][0]
        self.assertEqual(after['created_at'], before['created_at'])
        self.assertEqual(after['source'], '인포스탁 참고')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM stock_sector_events').fetchone()[0], 2)

    def test_failed_backfill_rolls_back_migration_and_retries(self):
        self.store.close()
        with sqlite3.connect(self.path) as db:
            for table in ('stock_sector_events', 'stock_sectors', 'sectors', 'stocks', 'schema_migrations'):
                db.execute(f'DROP TABLE {table}')
        with patch('kiwoom_collector.sectors.upsert_stock', side_effect=sqlite3.OperationalError('simulated failure')):
            with self.assertRaises(sqlite3.OperationalError):
                Store(self.path)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ranking_records').fetchone()[0], 3)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchone())
        retry = Store(self.path)
        try:
            self.assertEqual(SectorManager(retry).show('005930')['stock_name'], '삼성전자')
        finally:
            retry.close()
