import codecs
import csv
from datetime import datetime
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


class SectorCSVWorkflowTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'stock1.db'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.manager = SectorManager(self.store)
        at = datetime(2026, 10, 19, 9, tzinfo=KST)
        identifier = self.store.begin(at, 'mock', '1')
        self.store.finish(identifier, Snapshot(at, [
            {'bigd_rank': '1', 'stk_cd': '005930', 'stk_nm': '삼성전자'},
            {'bigd_rank': '2', 'stk_cd': '000660', 'stk_nm': 'SK하이닉스'},
            {'bigd_rank': '3', 'stk_cd': '035420', 'stk_nm': '네이버, "검토"'},
        ]))
        self.output = Path(self.temp.name) / 'data' / 'untagged.csv'

    def read(self):
        with self.output.open(encoding='utf-8-sig', newline='') as handle:
            return list(csv.DictReader(handle))

    def write(self, rows):
        with self.output.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=['stock_code', 'stock_name', 'sector_name'])
            writer.writeheader()
            writer.writerows(rows)

    def test_export_bom_codes_names_and_only_untagged_without_db_changes(self):
        self.manager.add('인터넷')
        self.manager.tag('035420', '인터넷', '기존 수동 검토')
        before = list(self.store.db.iterdump())
        result = self.manager.export_untagged(self.output)
        self.assertEqual(result['row_count'], 2)
        self.assertTrue(self.output.read_bytes().startswith(codecs.BOM_UTF8))
        rows = self.read()
        self.assertEqual([row['stock_code'] for row in rows], ['000660', '005930'])
        self.assertEqual(rows[0]['stock_name'], 'SK하이닉스')
        self.assertTrue(all(row['sector_name'] == '' for row in rows))
        self.assertEqual(list(self.store.db.iterdump()), before)

    def test_export_import_dry_run_multitag_and_summary_immediate_reflection(self):
        self.manager.add('인터넷')
        self.manager.tag('035420', '인터넷', '기존 출처')
        original_tag = self.manager.show('035420')['sectors']
        self.manager.export_untagged(self.output)
        rows = self.read()
        for row in rows:
            row['sector_name'] = '반도체'
        rows.append({'stock_code': '005930', 'stock_name': '편집된 참고 이름', 'sector_name': 'IT하드웨어'})
        self.write(rows)
        before = list(self.store.db.iterdump())
        report = self.manager.import_csv(self.output, source='인포스탁 참고·수동 검토', dry_run=True)
        self.assertFalse(report['applied'])
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['added'], 3)
        self.assertEqual(list(self.store.db.iterdump()), before)
        report = self.manager.import_csv(self.output, source='인포스탁 참고·수동 검토')
        self.assertTrue(report['applied'])
        summary = sector_summary(self.path)
        semiconductor = next(group for group in summary['sectors'] if group['sector_name'] == '반도체')
        self.assertEqual((semiconductor['stock_count'], semiconductor['label']), (2, '군집 후보'))
        self.assertEqual(summary['unique_stock_count'], 3)
        self.assertEqual(summary['unclassified_stock_count'], 0)
        self.assertEqual(self.manager.show('005930')['stock_name'], '삼성전자')
        self.assertEqual(len(self.manager.show('005930')['sectors']), 2)
        self.assertEqual(self.manager.show('035420')['sectors'], original_tag)
        self.assertEqual(self.store.status()['records'], 3)
        applied_state = list(self.store.db.iterdump())
        repeated = self.manager.import_csv(self.output, source='인포스탁 참고·수동 검토')
        self.assertEqual(repeated['unchanged'], 3)
        self.assertEqual(list(self.store.db.iterdump()), applied_state)

    def test_blank_names_and_duplicate_rows_are_errors_without_partial_apply(self):
        self.manager.export_untagged(self.output)
        before = list(self.store.db.iterdump())
        blank = self.manager.import_csv(self.output, dry_run=True)
        self.assertEqual(len(blank['errors']), 3)
        self.assertFalse(blank['applied'])
        rows = self.read()
        rows[0]['sector_name'] = '반도체'
        rows.append(dict(rows[0]))
        self.write(rows)
        report = self.manager.import_csv(self.output)
        self.assertFalse(report['applied'])
        self.assertTrue(any('중복' in error for error in report['errors']))
        self.assertEqual(list(self.store.db.iterdump()), before)

    def test_apply_revalidates_changed_file_after_successful_dry_run(self):
        self.manager.export_untagged(self.output)
        rows = self.read()
        for row in rows:
            row['sector_name'] = '검토 태그'
        self.write(rows)
        self.assertEqual(self.manager.import_csv(self.output, dry_run=True)['errors'], [])
        before = list(self.store.db.iterdump())
        rows[0]['stock_code'] = '660'  # Excel numeric conversion must not be silently padded.
        self.write(rows)
        report = self.manager.import_csv(self.output)
        self.assertFalse(report['applied'])
        self.assertIn('종목코드 형식', report['errors'][0])
        self.assertEqual(list(self.store.db.iterdump()), before)

    def test_unknown_stock_in_extended_file_blocks_everything(self):
        self.output.parent.mkdir()
        self.write([{'stock_code': '005930', 'stock_name': '삼성전자', 'sector_name': '반도체'},
                    {'stock_code': '999999', 'stock_name': '미등록', 'sector_name': '반도체'}])
        before = list(self.store.db.iterdump())
        report = self.manager.import_csv(self.output)
        self.assertFalse(report['applied'])
        self.assertIn('종목 마스터', report['errors'][0])
        self.assertEqual(list(self.store.db.iterdump()), before)

    def test_existing_output_database_and_env_are_not_overwritten(self):
        self.output.parent.mkdir()
        self.output.write_text('사용자 편집 파일', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '이미 존재'):
            self.manager.export_untagged(self.output)
        self.assertEqual(self.output.read_text(encoding='utf-8'), '사용자 편집 파일')
        env = Path(self.temp.name) / '.env'
        env.write_text('local-only-placeholder', encoding='utf-8')
        for path in (self.path, env):
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                self.manager.export_untagged(path)
            self.assertEqual(path.read_bytes(), before)

    def test_export_roundtrips_quoted_unicode_names_and_empty_result(self):
        self.manager.export_untagged(self.output)
        self.assertEqual(next(row for row in self.read() if row['stock_code'] == '035420')['stock_name'], '네이버, "검토"')
        self.manager.add('검토 태그')
        for code in ('005930', '000660', '035420'):
            self.manager.tag(code, '검토 태그')
        empty = self.output.with_name('empty.csv')
        self.assertEqual(self.manager.export_untagged(empty)['row_count'], 0)
        self.assertEqual(empty.read_text(encoding='utf-8-sig').strip(), 'stock_code,stock_name,sector_name')

    def test_extended_import_failure_rolls_back_all_tags_and_history(self):
        self.manager.export_untagged(self.output)
        rows = self.read()
        for row in rows:
            row['sector_name'] = '신규 태그'
        self.write(rows)
        before = list(self.store.db.iterdump())
        tag = self.manager._tag
        calls = [0]
        def fail(*args):
            calls[0] += 1
            if calls[0] == 2:
                raise sqlite3.IntegrityError('injected failure')
            return tag(*args)
        with patch.object(self.manager, '_tag', side_effect=fail), self.assertRaises(sqlite3.IntegrityError):
            self.manager.import_csv(self.output)
        self.assertEqual(list(self.store.db.iterdump()), before)

    def test_cli_export_and_import_syntax_offline(self):
        with patch('kiwoom_collector.__main__.load_credentials', side_effect=AssertionError('no keys')), patch('kiwoom_collector.client.requests.Session', side_effect=AssertionError('no API')), patch('builtins.print') as output:
            self.assertEqual(main(['sector', 'export-untagged', '--output', str(self.output), '--db', str(self.path)]), 0)
            rows = self.read()
            for row in rows:
                row['sector_name'] = '수동 태그'
            self.write(rows)
            self.assertEqual(main(['sector', 'import', str(self.output), '--dry-run', '--source', '수동 CSV', '--db', str(self.path)]), 0)
            self.assertTrue(json.loads(output.call_args.args[0])['dry_run'])
            self.assertEqual(main(['sector', 'import', str(self.output), '--source', '수동 CSV', '--db', str(self.path)]), 0)
            self.assertEqual(main(['sector', 'summary', '--db', str(self.path)]), 0)
            self.assertEqual(json.loads(output.call_args.args[0])['unclassified_stock_count'], 0)

    def test_export_write_failure_closes_and_removes_only_new_partial_file(self):
        before = list(self.store.db.iterdump())
        with patch('kiwoom_collector.sectors.csv.DictWriter.writerow', side_effect=OSError('simulated write failure')):
            with self.assertRaises(OSError):
                self.manager.export_untagged(self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.store.db.iterdump()), before)
        # No open output handle may prevent immediate creation/deletion on Windows.
        self.output.write_text('편집본', encoding='utf-8')
        self.output.unlink()
