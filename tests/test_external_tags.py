"""External classification imports are offline and preserve all old observations."""
from contextlib import closing
import codecs
import csv
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_collector.client import KST, Snapshot
from kiwoom_collector.storage import Store
from kiwoom_collector.sectors import SectorManager
from kiwoom_collector.external_tags import (import_external, add_alias, export_priority, provenance,
    external_command, coverage, observations)
from kiwoom_collector.conditions import migrate_conditions, condition_report
from kiwoom_collector.summary import read_database, sector_summary
from kiwoom_collector.__main__ import main

DEFAULTS = {'source':'핀업','observed_at':'2026-10-18','terms_url':'https://example.com/terms',
            'terms_note':'사용자가 확인한 이용 범위 메모 (테스트 자료)','terms_checked_at':'2026-10-18'}


class ExternalTagsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'stock1.db'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.db = self.store.db
        self.manager = SectorManager(self.store)
        self.at = datetime(2026,10,19,9,40,tzinfo=KST)
        self.ranking = self.store.begin(self.at,'mock','1')
        self.store.finish(self.ranking,Snapshot(self.at,[
            {'bigd_rank':'1','stk_cd':'005930','stk_nm':'삼성전자'},
            {'bigd_rank':'2','stk_cd':'000660','stk_nm':'SK하이닉스'}]))
        self.csv_path = Path(self.temp.name)/'themes.csv'

    def write(self, rows, columns=('stock_code','sector_name')):
        with self.csv_path.open('w',encoding='utf-8-sig',newline='') as handle:
            w = csv.DictWriter(handle,fieldnames=columns)
            w.writeheader(); w.writerows(rows)
        return self.csv_path

    def import_(self, source='핀업', observed_at='2026-10-18', dry_run=False):
        return import_external(self.db,self.csv_path,dict(DEFAULTS,source=source,observed_at=observed_at),dry_run=dry_run)

    def dump(self): return list(self.db.iterdump())

    def conditions(self):
        migrate_conditions(self.db)
        with self.db:
            self.db.execute("INSERT INTO condition_definitions VALUES(1,'mock','sector_seed','주도주종베','7','7','2026-10-19T09:40:00+09:00')")
            self.db.execute("""INSERT INTO condition_runs(id,batch_id,definition_id,requested_at,completed_at,status,record_count)
                VALUES('run','batch',1,'2026-10-19T09:40:00+09:00','2026-10-19T09:40:01+09:00','success',2)""")
            self.db.execute("INSERT INTO condition_members VALUES('run','005930','A005930','삼성전자','{}')")
            self.db.execute("INSERT INTO condition_members VALUES('run','035420','A035420','NAVER','{}')")

    def test_readonly_dry_run_on_unmigrated_database_changes_nothing(self):
        self.write([{'stock_code':'005930','sector_name':'반도체'}])
        before = self.dump()
        with read_database(self.path) as (db,_):
            report = import_external(db,self.csv_path,DEFAULTS,dry_run=True)
        self.assertEqual(before,self.dump())
        self.assertFalse(report['applied'])
        self.assertEqual(report['counts']['new_tags'],1)
        self.assertEqual(report['counts']['new_evidence'],1)
        self.assertEqual(report['before']['observed']['unclassified_percent'],100)

    def test_multisource_preserves_manual_tag_and_every_history_row(self):
        self.manager.add('반도체'); self.manager.tag('005930','반도체','수동')
        old = self.manager.show('005930')['sectors']
        old_history = list(self.db.execute('SELECT * FROM stock_sector_events').fetchall())
        self.write([{'stock_code':'005930','sector_name':'반도체'}])
        preview = self.import_(dry_run=True)
        self.assertEqual(preview['conflicts'][0]['kind'],'additional_source')
        self.assertEqual(preview['existing_tags'][0]['stock_code'],'005930')
        one = self.import_()
        two = self.import_(source='인포스탁')
        self.assertTrue(one['applied'] and two['applied'])
        self.assertEqual(old,self.manager.show('005930')['sectors'])
        self.assertEqual(old_history,list(self.db.execute('SELECT * FROM stock_sector_events').fetchall()))
        records = provenance(self.db,'005930')
        self.assertEqual({r['source'] for r in records['evidence']},{'핀업','인포스탁'})
        self.assertTrue(all(r['observed_at']=='2026-10-18' and r['registered_at'] for r in records['evidence']))
        self.assertIsNone(records['current_tags'][0]['observed_at'])

    def test_new_dated_confirmation_appends_without_overwrite(self):
        self.write([{'stock_code':'005930','sector_name':'반도체'}])
        self.import_()
        first = dict(self.db.execute('SELECT * FROM sector_tag_evidence').fetchone())
        self.import_(observed_at='2026-10-19')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sector_tag_evidence').fetchone()[0],2)
        self.assertEqual(dict(self.db.execute('SELECT * FROM sector_tag_evidence ORDER BY evidence_id LIMIT 1').fetchone()),first)
        self.assertEqual(len(self.manager.show('005930')['sectors']),1)

    def test_repeat_is_idempotent_and_terms_change_requires_review(self):
        self.write([{'stock_code':'005930','sector_name':'반도체'}]); self.import_()
        repeated = self.import_()
        self.assertEqual(repeated['counts']['existing_evidence'],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sector_tag_evidence').fetchone()[0],1)
        before = self.dump()
        report = import_external(self.db,self.csv_path,dict(DEFAULTS,terms_note='다른 메모'))
        self.assertTrue(report['errors'])
        self.assertFalse(report['applied'])
        self.assertEqual(self.dump(),before)

    def test_new_source_terms_revision_does_not_modify_old_terms(self):
        self.write([{'stock_code':'005930','sector_name':'반도체'}]); self.import_()
        old = dict(self.db.execute('SELECT * FROM sector_source_terms').fetchone())
        import_external(self.db,self.csv_path,dict(DEFAULTS,observed_at='2026-10-19',terms_note='새 확인 내용',terms_checked_at='2026-10-19'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sector_source_terms').fetchone()[0],2)
        self.assertEqual(dict(self.db.execute('SELECT * FROM sector_source_terms ORDER BY terms_id LIMIT 1').fetchone()),old)

    def test_alias_is_explicit_scoped_and_never_reassigned(self):
        self.manager.add('이차전지'); self.manager.add('배터리 소재')
        add_alias(self.db,'2차전지','이차전지')
        add_alias(self.db,'2차전지','배터리 소재','핀업')
        self.write([{'stock_code':'005930','sector_name':'2차전지'}])
        self.import_(); self.import_(source='인포스탁')
        self.assertEqual({r['sector_name'] for r in self.manager.show('005930')['sectors']},{'이차전지','배터리 소재'})
        before = self.dump()
        with self.assertRaises(ValueError): add_alias(self.db,'2차전지','이차전지','핀업')
        self.assertEqual(before,self.dump())
        with self.assertRaises(ValueError): add_alias(self.db,'이차전지','배터리 소재')
        self.assertTrue(all(r['input_sector_name']=='2차전지' for r in provenance(self.db,'005930')['evidence']))

    def test_duplicate_after_alias_normalization_blocks_all(self):
        self.manager.add('반도체'); add_alias(self.db,'반도체주','반도체','핀업')
        self.write([{'stock_code':'005930','sector_name':'반도체'},{'stock_code':'005930','sector_name':'반도체주'}])
        before = self.dump(); result = self.import_()
        self.assertTrue(result['errors']); self.assertEqual(before,self.dump())

    def test_batch_multiple_sources_and_multitag_conflicts_are_non_destructive(self):
        self.write([{'stock_code':'005930','sector_name':'반도체','source':'핀업'},
                    {'stock_code':'005930','sector_name':'반도체','source':'인포스탁'},
                    {'stock_code':'005930','sector_name':'AI','source':'인포스탁'}],('stock_code','sector_name','source'))
        report = self.import_()
        self.assertEqual(report['counts']['new_tags'],2)
        self.assertEqual(report['counts']['new_evidence'],3)
        self.assertEqual({r['kind'] for r in report['conflicts']},{'additional_source','different_theme'})
        self.assertEqual(len(self.manager.show('005930')['sectors']),2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sector_tag_evidence').fetchone()[0],3)

    def test_validation_errors_roll_back_all_and_create_no_schema(self):
        self.write([{'stock_code':'005930','sector_name':'반도체'},
                    {'stock_code':'5930','sector_name':'반도체'},
                    {'stock_code':'999999','sector_name':'반도체'}])
        before = self.dump(); report = self.import_()
        self.assertFalse(report['applied']); self.assertEqual(len(report['errors']),2)
        self.assertEqual(before,self.dump())

    def test_bad_metadata_rejected_without_network(self):
        self.write([{'stock_code':'005930','sector_name':'반도체'}])
        for override in ({'observed_at':'2099-01-01'},{'observed_at':'20261018'},
                         {'terms_checked_at':'bad'},{'terms_url':'https://user:password@example.com/'},
                         {'terms_url':'file:///tmp/terms'},{'terms_note':''},{'source':''}):
            with self.subTest(override=override):
                before = self.dump()
                report = import_external(self.db,self.csv_path,dict(DEFAULTS,**override))
                self.assertTrue(report['errors']); self.assertEqual(before,self.dump())

    def test_unknown_stock_can_be_added_with_reviewed_name_existing_names_untouched(self):
        self.write([{'stock_code':'035420','stock_name':'NAVER','sector_name':'인터넷'},
                    {'stock_code':'005930','stock_name':'다른 이름','sector_name':'반도체'}],('stock_code','stock_name','sector_name'))
        result = self.import_()
        self.assertEqual(result['new_stocks'],[{'stock_code':'035420','stock_name':'NAVER'}])
        self.assertEqual(self.manager.show('005930')['stock_name'],'삼성전자')
        self.assertEqual(self.manager.show('035420')['stock_name'],'NAVER')

    def test_atomic_runtime_failure_rolls_back_schema_sources_stocks_and_audit(self):
        self.write([{'stock_code':'035420','stock_name':'NAVER','sector_name':'인터넷'}],('stock_code','stock_name','sector_name'))
        before = self.dump()
        with patch('kiwoom_collector.external_tags.SectorManager._tag',side_effect=sqlite3.IntegrityError('injected')):
            with self.assertRaises(sqlite3.IntegrityError): self.import_()
        self.assertEqual(before,self.dump())
        self.assertTrue(self.import_()['applied'])  # Retry succeeds after real rollback.
        self.assertEqual(self.db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_comparison_report_fixed_universe_and_summary_conditions_reflect_tags(self):
        self.conditions()
        old_collections = [dict(r) for r in self.db.execute('SELECT * FROM collections')]
        old_conditions = [dict(r) for r in self.db.execute('SELECT * FROM condition_runs')]
        self.write([{'stock_code':'005930','sector_name':'반도체'}, {'stock_code':'000660','sector_name':'반도체'}])
        report = self.import_()
        self.assertEqual(report['before']['master']['unclassified_percent'],100)
        self.assertEqual(report['after']['master']['unclassified_percent'],0)
        self.assertEqual(report['before']['observed']['stock_count'],3)
        self.assertAlmostEqual(report['after']['observed']['unclassified_percent'],100/3)
        self.assertEqual(report['after']['sectors']['반도체'],{'master_count':2,'observed_count':2})
        self.assertEqual(sector_summary(self.path)['unclassified_stock_count'],0)
        self.assertEqual(condition_report(self.path)['sectors'][0]['stock_count'],1)
        saved = json.loads(self.db.execute('SELECT report_json FROM sector_external_imports').fetchone()[0])
        self.assertEqual(saved,report)
        self.assertEqual(old_collections,[dict(r) for r in self.db.execute('SELECT * FROM collections')])
        self.assertEqual(old_conditions,[dict(r) for r in self.db.execute('SELECT * FROM condition_runs')])

    def test_priority_export_bom_union_dedup_order_and_legacy_compatible(self):
        self.conditions()
        output = Path(self.temp.name)/'priority.csv'
        before = self.dump()
        with read_database(self.path) as (db,_): report = export_priority(db,output,'mock')
        self.assertEqual(before,self.dump())
        self.assertEqual(report['row_count'],3)
        self.assertTrue(output.read_bytes().startswith(codecs.BOM_UTF8))
        with output.open(encoding='utf-8-sig',newline='') as handle:
            reader = csv.DictReader(handle); rows = list(reader)
        self.assertEqual(reader.fieldnames,['stock_code','stock_name','sector_name'])
        self.assertEqual(rows[0]['stock_code'],'005930')
        self.assertEqual({r['stock_code'] for r in rows},{'005930','000660','035420'})
        self.assertFalse(next(r for r in report['priorities'] if r['stock_code']=='035420')['in_master'])
        self.manager.add('반도체'); self.manager.tag('005930','반도체')
        other = output.with_name('filtered.csv')
        self.assertEqual(export_priority(self.db,other,'mock')['row_count'],2)
        with self.assertRaises(ValueError): export_priority(self.db,output,'mock')

    def test_priority_excludes_test_failed_other_environment_and_unsupported(self):
        from datetime import timedelta
        for i,(environment,test_mode) in enumerate((('mock',True),('real',False))):
            at = self.at+timedelta(minutes=i+1)
            run = self.store.begin(at,environment,'1',test_mode=test_mode)
            self.store.finish(run,Snapshot(at,[{'bigd_rank':'1','stk_cd':'035420','stk_nm':'NAVER'}]))
        out = Path(self.temp.name)/'priority.csv'
        report = export_priority(self.db,out,'mock')
        self.assertEqual(report['row_count'],2)
        self.assertFalse(any(r['stock_code']=='035420' for r in report['priorities']))
        empty = export_priority(self.db,out.with_name('empty.csv'),'real')
        self.assertEqual(empty['row_count'],1)

    def test_original_legacy_csv_now_refuses_cross_source_overwrite(self):
        self.manager.add('반도체'); self.manager.tag('005930','반도체','수동')
        self.write([{'stock_code':'005930','sector_name':'반도체'},{'stock_code':'000660','sector_name':'반도체'}])
        before = self.dump()
        report = self.manager.import_csv(self.csv_path,source='핀업')
        self.assertFalse(report['applied']); self.assertTrue(report['conflicts'])
        self.assertEqual(before,self.dump())

    def test_cli_metadata_import_export_alias_provenance_and_report_offline(self):
        self.write([{'stock_code':'005930','sector_name':'반도체'}])
        opts = ['--source','핀업','--observed-at','2026-10-18','--terms-url','https://example.com/terms',
                '--terms-note','직접 확인 메모','--terms-checked-at','2026-10-18','--db',str(self.path)]
        with patch('kiwoom_collector.__main__.load_credentials',side_effect=AssertionError('no credentials')), \
             patch('kiwoom_collector.client.requests.Session',side_effect=AssertionError('no HTTP')), \
             patch('builtins.print') as output:
            before = self.dump()
            self.assertEqual(main(['sector','import',str(self.csv_path),*opts,'--dry-run']),0)
            self.assertEqual(before,self.dump())
            self.assertEqual(main(['sector','import',str(self.csv_path),*opts]),0)
            self.assertEqual(main(['sector','alias','add','반도체주','반도체','--source','핀업','--db',str(self.path)]),0)
            for args in (['alias','list'],['provenance','005930'],['import-report','latest']):
                self.assertEqual(main(['sector',*args,'--db',str(self.path)]),0)
            target = Path(self.temp.name)/'report.json'
            self.assertEqual(main(['sector','import-report','latest','--output',str(target),'--db',str(self.path)]),0)
            self.assertTrue(json.loads(target.read_text(encoding='utf-8'))['applied'])
            self.assertEqual(main(['sector','export-priority','--output',str(target.with_suffix('.csv')),'--db',str(self.path)]),0)

    def test_row_metadata_selects_external_mode_without_extra_options(self):
        columns = ('stock_code','sector_name','source','observed_at','terms_url','terms_note','terms_checked_at')
        self.write([dict(DEFAULTS,stock_code='005930',sector_name='반도체')],columns)
        with patch('builtins.print'):
            self.assertEqual(main(['sector','import',str(self.csv_path),'--dry-run','--db',str(self.path)]),0)
            self.assertEqual(main(['sector','import',str(self.csv_path),'--db',str(self.path)]),0)
        self.assertEqual(len(provenance(self.db,'005930')['evidence']),1)

    def test_export_failure_closes_handle_and_removes_partial_file(self):
        output = Path(self.temp.name)/'priority.csv'
        with patch('kiwoom_collector.external_tags.csv.DictWriter.writerow',side_effect=OSError('injected')):
            with self.assertRaises(OSError): export_priority(self.db,output,'mock')
        self.assertFalse(output.exists())
        output.write_text('retry',encoding='utf-8'); output.unlink()

    def test_readonly_operations_missing_db_do_not_initialize(self):
        missing = Path(self.temp.name)/'missing.db'
        for args in (['export-priority'],['alias','list'],['provenance','005930'],['import-report','latest']):
            with self.assertRaises(ValueError): external_command(missing,args,output=missing.with_suffix('.csv') if args==['export-priority'] else None)
        self.assertFalse(missing.exists())


if __name__ == '__main__': unittest.main()
