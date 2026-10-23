"""Exercise the real python -m entrypoint in isolated, network-blocked processes."""
from contextlib import closing
from datetime import datetime
import csv
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from kiwoom_collector.client import KST, Snapshot
from kiwoom_collector.conditions import migrate_conditions
from kiwoom_collector.storage import Store

ROOT = Path(__file__).resolve().parents[1]


class CLIEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.db = self.folder/'stock1.db'
        # Close every fixture handle before launching a child (including on Windows).
        with closing(Store(self.db)) as store:
            at = datetime(2026,10,19,9,40,tzinfo=KST)
            identifier = store.begin(at,'mock','1')
            store.finish(identifier,Snapshot(at,[{'bigd_rank':'1','stk_cd':'005930','stk_nm':'삼성전자'},
                                                  {'bigd_rank':'2','stk_cd':'000660','stk_nm':'SK하이닉스'}]))
            migrate_conditions(store.db)
            with store.db:
                store.db.execute("INSERT INTO condition_definitions VALUES(1,'mock','sector_seed','주도주종베','7','7','2026-10-19T09:40:00+09:00')")
                store.db.execute("""INSERT INTO condition_runs(id,batch_id,definition_id,requested_at,completed_at,status,record_count)
                    VALUES('fixture-run','fixture-batch',1,'2026-10-19T09:40:00+09:00','2026-10-19T09:40:01+09:00','success',1)""")
                store.db.execute("INSERT INTO condition_members VALUES('fixture-run','005930','A005930','삼성전자','{}')")
        self.csv = self.folder/'themes.csv'
        self.csv.write_text('stock_code,sector_name\n005930,반도체\n',encoding='utf-8-sig')
        self.guard = self.folder/'guard'
        self.guard.mkdir()
        (self.guard/'sitecustomize.py').write_text('''import socket

def forbidden(*args, **kwargs):
    raise RuntimeError("OFFLINE_CLI_TEST_NETWORK_FORBIDDEN")
socket.create_connection = forbidden
socket.getaddrinfo = forbidden
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
''',encoding='utf-8')
        self.env = os.environ.copy()
        self.env['PYTHONPATH'] = os.pathsep.join((str(self.guard),str(ROOT)))
        self.env['PYTHONIOENCODING'] = 'utf-8'
        self.env['PYTHONDONTWRITEBYTECODE'] = '1'
        for key in ('KIWOOM_APP_KEY','KIWOOM_SECRET_KEY','KIWOOM_ENV'):
            self.env.pop(key,None)
        self.opts = ['--source','핀업','--observed-at','2026-10-18','--terms-url','https://example.com/terms',
                     '--terms-note','테스트용 사용자 확인 메모','--terms-checked-at','2026-10-18']

    def run_cli(self,*args,expected=0):
        result = subprocess.run([sys.executable,'-m','kiwoom_collector',*map(str,args)],cwd=ROOT,
            env=self.env,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,expected,f'{args}\nstdout={result.stdout}\nstderr={result.stderr}')
        self.assertNotIn('OFFLINE_CLI_TEST_NETWORK_FORBIDDEN',result.stdout+result.stderr)
        return result

    def cli(self,*args,expected=0):
        return self.run_cli(*args,'--db',self.db,expected=expected)

    def dump(self):
        with closing(sqlite3.connect(self.db)) as db:
            return list(db.iterdump())

    def test_reported_commands_work_through_real_module_entrypoint(self):
        before = self.dump()
        output = self.folder/'priority.csv'
        priority = json.loads(self.cli('sector','export-priority','--environment','mock','--output',output).stdout)
        self.assertEqual(priority['row_count'],2)
        with output.open(encoding='utf-8-sig',newline='') as handle:
            self.assertEqual(next(csv.DictReader(handle))['stock_code'],'005930')
        preview = json.loads(self.cli('sector','import',self.csv,*self.opts,'--dry-run').stdout)
        self.assertEqual(preview['errors'],[])
        self.assertFalse(preview['applied'])
        self.assertEqual(before,self.dump())
        result = json.loads(self.cli('sector','import',self.csv,*self.opts).stdout)
        self.assertTrue(result['applied'])
        evidence = json.loads(self.cli('sector','provenance','005930').stdout)['evidence']
        self.assertEqual(evidence[0]['source'],'핀업')
        self.assertEqual(evidence[0]['terms_url'],'https://example.com/terms')
        report_path = self.folder/'report.json'
        report = json.loads(self.cli('sector','import-report','latest','--output',report_path).stdout)
        self.assertEqual(report,result)
        self.assertEqual(json.loads(report_path.read_text(encoding='utf-8')),result)
        self.assertEqual(json.loads(self.cli('condition','report','--environment','mock').stdout)['sectors'][0]['stock_count'],1)
        self.assertEqual(json.loads(self.cli('sector','summary').stdout)['unclassified_stock_count'],1)
        # Existing observations and condition batches are byte-for-byte unchanged at SQL-row level.
        after = self.dump()
        for statement in before:
            if statement.startswith(('INSERT INTO "collections"','INSERT INTO "ranking_records"','INSERT INTO "condition_')):
                self.assertIn(statement,after)

    def test_missing_unconfirmed_terms_never_become_confirmed_records(self):
        before = self.dump()
        variants = [[], ['--terms-url',''], ['--terms-note',''], ['--terms-checked-at','']]
        for override in variants:
            for dry in ([],['--dry-run']):
                with self.subTest(override=override,dry=dry):
                    opts = self.opts if override else ['--source','핀업','--observed-at','2026-10-18']
                    report = json.loads(self.cli('sector','import',self.csv,*opts,*override,*dry,expected=1).stdout)
                    self.assertTrue(report['errors'])
                    self.assertFalse(report['applied'])
                    self.assertEqual(self.dump(),before)
        self.cli('sector','provenance','005930')
        self.assertEqual(self.dump(),before)

    def test_row_metadata_and_cli_options_are_accepted_in_any_position(self):
        self.csv.write_text('stock_code,sector_name,source,observed_at,terms_url,terms_note,terms_checked_at\n'
            '005930,반도체,핀업,2026-10-18,https://example.com/terms,테스트 확인,2026-10-18\n',encoding='utf-8-sig')
        self.run_cli('--db',self.db,'sector','--dry-run','import',self.csv)
        self.run_cli('--db',self.db,'sector','import','--environment','mock',self.csv)
        self.run_cli('--db',self.db,'sector','alias','--source','핀업','add','반도체주','반도체')
        aliases = json.loads(self.cli('sector','alias','list').stdout)
        self.assertEqual(aliases[0]['alias'],'반도체주')
        self.run_cli('--db',self.db,'sector','--environment','mock','export-priority','--output',self.folder/'priority.csv')

    def test_unsupported_option_scopes_fail_before_accessing_database(self):
        cases = [
            ['sector','provenance','005930','--environment','mock'],
            ['sector','import-report','latest','--environment','mock'],
            ['sector','alias','list','--environment','mock'],
            ['condition','fetch','--environment','mock'],
            ['condition','list','--environment','real'],
            ['status','--observed-at','2026-10-18'],
            ['sector','export-priority','--terms-note','x'],
            ['sector','provenance','005930','--output',str(self.folder/'x.json')],
            ['sector','alias','list','--source','핀업'],
            ['condition','report','--dry-run'], ['status','--qry-tp','1'],
            ['sector','export-priority','--date','2026-10-19'],
            ['once','--interval','300'],['once','--test-now'],
            ['collect','--schedule','--interval','60'],
            ['sector','export-priority'],['sector','alias','add','a'],
        ]
        before = self.dump()
        for args in cases:
            with self.subTest(args=args): self.cli(*args,expected=2)
        self.assertEqual(before,self.dump())

    def test_legacy_local_commands_and_analysis_are_still_routed(self):
        self.cli('sector','add','반도체')
        self.cli('sector','tag','005930','반도체','--source','수동')
        self.cli('sector','show','005930')
        self.cli('sector','list')
        self.cli('sector','untagged')
        self.cli('sector','export-untagged','--output',self.folder/'untagged.csv')
        self.cli('sector','timeline','--date','2026-10-19','--environment','mock')
        self.cli('sector','dashboard','--date','2026-10-19','--environment','mock','--output',self.folder/'dashboard.html')
        self.cli('status'); self.cli('latest')
        self.cli('sector','untag','005930','반도체')
        self.cli('sector','import',self.csv,'--source','CSV','--dry-run')
        self.cli('sector','import',self.csv,'--source','CSV')

    def test_output_overwrite_and_invalid_metadata_preserve_database(self):
        output = self.folder/'priority.csv'
        output.write_text('user edit',encoding='utf-8')
        before = self.dump()
        self.cli('sector','export-priority','--environment','mock','--output',output,expected=1)
        self.assertEqual(output.read_text(encoding='utf-8'),'user edit')
        self.cli('sector','import',self.csv,*self.opts,'--observed-at','2099-01-01',expected=1)
        self.assertEqual(before,self.dump())

    def test_workbench_real_entrypoint_is_readonly_and_accepts_scoped_options(self):
        before = self.dump()
        output = self.folder/'workbench.html'
        result = json.loads(self.cli('sector','workbench','--environment','mock','--output',output).stdout)
        self.assertEqual(result['stock_count'],2)
        self.assertIn('workspace-data',output.read_text(encoding='utf-8'))
        self.assertEqual(before,self.dump())
        self.cli('sector','workbench',expected=2)
        self.cli('sector','workbench','--output',self.folder/'other.html','--source','x',expected=2)

    def test_radar_real_entrypoint_is_offline_and_readonly(self):
        before = self.db.read_bytes()
        output = self.folder/'radar.html'
        result = json.loads(self.cli('sector','radar','--date','2026-10-19',
                                     '--environment','mock','--output',output).stdout)
        self.assertEqual(result['collections'],1)
        self.assertIn('시세 미수집',output.read_text(encoding='utf-8'))
        self.assertEqual(before,self.db.read_bytes())
        self.cli('sector','radar',expected=2)
        self.cli('sector','radar','--output',output,'--source','x',expected=2)

    def test_help_documents_new_metadata_options(self):
        result = self.run_cli('--help')
        for option in ('--observed-at','--terms-url','--terms-note','--terms-checked-at','export-priority','import-report'):
            self.assertIn(option,result.stdout)


if __name__ == '__main__': unittest.main()
