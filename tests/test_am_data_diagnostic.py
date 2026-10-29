"""C8 synthetic-only diagnostics, including per-stock ka10095 raw timestamps."""
from contextlib import closing
from copy import deepcopy
import io
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import test_am_input_adapter as fixtures
from kiwoom_collector import am_data_diagnostic as d, am_input_adapter as a, am_momentum as am
CUTOFF=fixtures.CUTOFF


class AMDataDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.AMInputAdapterTests('test_full_synthetic_sqlite_to_three_sector_briefing_and_growth')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.db,self.root=self.fixture.db,self.fixture.root
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('ALTER TABLE quote_observations ADD COLUMN raw_json TEXT')
            db.execute('ALTER TABLE quote_observations ADD COLUMN trde_qty TEXT')
            db.execute("UPDATE quote_batches SET api_id='ka10095'")
            for row in db.execute('SELECT rowid,requested_code,date,tm FROM quote_observations').fetchall():
                raw=dict(stk_cd=row[1],dt=row[2],cntr_tm=row[3],trde_qty='100000',
                         token='DO_NOT_EXPORT_PRIVATE_AUTH',account='DO_NOT_EXPORT_ACCOUNT')
                db.execute("UPDATE quote_observations SET api_id='ka10095',raw_json=?,trde_qty='100000' WHERE rowid=?",
                           (json.dumps(raw),row[0]))
        self.refresh()

    def refresh(self):
        self.fixture.manifest=self.fixture.make_manifest()
        for _,proof in self.fixture.proofs('quote'): proof['units']['trde_qty']='shares'

    def diagnose(self, **kwargs):
        options=dict(trade_date='2026-10-29',cutoff_at=CUTOFF,environment='mock',synthetic=True,
                     capital_json=self.fixture.capital,evidence=self.fixture.manifest)
        options.update(kwargs)
        return d.diagnose(self.db,**options)

    def mutate_raw(self, **changes):
        with closing(sqlite3.connect(self.db)) as db, db:
            for row in db.execute('SELECT rowid,raw_json FROM quote_observations').fetchall():
                raw=json.loads(row[1]);raw.update(changes)
                db.execute('UPDATE quote_observations SET raw_json=? WHERE rowid=?',(json.dumps(raw),row[0]))
        self.refresh()

    def test_full_synthetic_one_pass_checks_readiness_and_separate_times(self):
        report=self.diagnose()
        self.assertTrue(all(s['stored_top20_complete'] and s['verified_top20_complete'] for s in report['ranking_slots']))
        self.assertEqual(len(report['quotes']),18)
        self.assertTrue(all(q['verified'] for q in report['quotes']))
        self.assertTrue(report['am_selection']['ready_for_verified_briefing'])
        self.assertEqual(report['am_selection']['engine_sector_count'],3)
        self.assertEqual(report['capital']['connected_stock_count'],9)
        self.assertIn('합성 테스트 데이터',report['summary'])
        self.assertEqual(report['quotes'][0]['values']['trde_qty']['declared_unit'],'shares')

    def test_stored_complete_does_not_invent_receipt_or_unit_verification(self):
        report=self.diagnose(evidence=None)
        self.assertTrue(all(s['stored_top20_complete'] for s in report['ranking_slots']))
        self.assertTrue(all(not s['verified_top20_complete'] and s['response_received_at'] is None for s in report['ranking_slots']))
        self.assertTrue(all(q['response_received_at'] is None for q in report['quotes']))
        self.assertFalse(report['am_selection']['ready_for_verified_briefing'])
        self.assertIn('관심도 관측·수신 시각 미검증',report['summary'])

    def test_no_shared_batch_time_substitutes_missing_stock_time(self):
        self.mutate_raw(dt=None,cntr_tm=None)
        report=self.diagnose()
        self.assertTrue(all(q['raw_market_observed_at'] is None for q in report['quotes']))
        self.assertTrue(all(q['local_request_finished_at'] for q in report['quotes']))
        self.assertFalse(report['am_selection']['ready_for_verified_briefing'])

    def test_raw_execution_time_cannot_be_changed_to_match_batch_receipt(self):
        self.mutate_raw(cntr_tm='094900')
        report=self.diagnose()
        self.assertTrue(all('market_time_does_not_match_review' in q['missing_evidence'] for q in report['quotes']))
        self.assertFalse(report['am_selection']['ready_for_verified_briefing'])

    def test_previous_day_and_cutoff_quote_time_are_not_joined(self):
        for changes in [dict(dt='20261028'),dict(dt='20261029',cntr_tm='095001')]:
            self.mutate_raw(**changes)
            report=self.diagnose()
            self.assertFalse(report['am_selection']['ready_for_verified_briefing'])
            self.assertTrue(all('market_time_wrong_day_or_after_cutoff' in q['missing_evidence'] for q in report['quotes']))

    def test_missing_volume_and_wrong_units_remain_missing_evidence(self):
        for _,proof in self.fixture.proofs('quote'): proof['units']['trde_qty']='thousand_shares'
        report=self.diagnose()
        self.assertTrue(all('trde_qty_unit_unverified' in q['missing_evidence'] for q in report['quotes']))
        self.assertFalse(report['am_selection']['ready_for_verified_briefing'])
        self.mutate_raw(trde_qty='not-a-number')
        self.assertTrue(all('trde_qty_missing_or_invalid' in q['missing_evidence'] for q in self.diagnose()['quotes']))

    def test_duplicate_or_incomplete_top20_is_classified_not_filled(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("DELETE FROM ranking_records WHERE collection_id='run0' AND rank=20")
        self.refresh(); report=self.diagnose()
        self.assertFalse(report['ranking_slots'][0]['stored_top20_complete'])
        self.assertIn('top20_incomplete_or_duplicate',report['ranking_slots'][0]['missing_evidence'])
        self.assertFalse(report['am_selection']['ready_for_verified_briefing'])

    def test_duplicate_successful_slot_is_explicit(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("INSERT INTO collections SELECT 'duplicate',collected_at,scheduled_at,environment,api_id,qry_tp,test_mode,status,record_count FROM collections WHERE id='run0'")
        self.refresh(); report=self.diagnose()
        self.assertIn('duplicate_successful_slot',report['ranking_slots'][0]['missing_evidence'])

    def test_other_environment_or_day_never_promoted(self):
        for options in [dict(environment='real'),dict(trade_date='2026-10-28',cutoff_at='2026-10-28T09:50:00+09:00')]:
            report=self.diagnose(**options)
            self.assertEqual(report['quotes'],[])
            self.assertFalse(report['am_selection']['ready_for_verified_briefing'])

    def test_raw_code_mismatch_blocks_readiness(self):
        self.mutate_raw(stk_cd='999999')
        self.assertTrue(all('raw_stock_code_mismatch' in q['missing_evidence'] for q in self.diagnose()['quotes']))
        self.assertFalse(self.diagnose()['am_selection']['ready_for_verified_briefing'])

    def test_raw_value_conflict_and_invalid_signed_price_do_not_pass(self):
        self.mutate_raw(cur_prc='999')
        self.assertTrue(all('stored_and_raw_market_values_disagree' in q['missing_evidence'] for q in self.diagnose()['quotes']))
        with closing(sqlite3.connect(self.db)) as db,db:
            db.execute("UPDATE quote_observations SET cur_prc='-10000'")
        self.refresh()
        self.assertTrue(all('cur_prc_missing_or_invalid' in q['missing_evidence'] for q in self.diagnose()['quotes']))

    def test_0186_empty_run_still_reports_actual_stored_page_times(self):
        from kiwoom_collector.turnover_rank import SCHEMA
        path=self.root/'turnover.db'
        with closing(sqlite3.connect(path)) as db,db:
            db.executescript(SCHEMA)
            db.execute('''INSERT INTO turnover_runs(id,api_id,environment,source_kind,test_mode,spec_version,
                request_json,profile_hash,created_at,status,issues_json,request_started_at,request_finished_at,row_count)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',('empty','ka10032','mock','fixture',1,'synthetic',
                json.dumps(dict(stex_tp='1',mrkt_tp='000',mang_stk_incls='0')),'synthetic',CUTOFF,'empty','[]',CUTOFF,CUTOFF,0))
            db.execute('''INSERT INTO turnover_pages(run_id,page_no,started_at,finished_at,http_status,return_code,
                status,row_count,issues_json,response_json) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                ('empty',1,CUTOFF,CUTOFF,200,0,'success',0,'[]','{}'))
        before=path.read_bytes();report=self.diagnose(turnover_db=path)
        self.assertEqual(len(report['turnover_runs']),1)
        page=report['turnover_runs'][0]['pages'][0]
        self.assertEqual(page['local_request_finished_at'],CUTOFF)
        self.assertIsNone(page['response_received_at'])
        self.assertIn('run_response_receipt_unverified',page['missing_evidence'])
        self.assertEqual(path.read_bytes(),before)

    def test_sector_available_after_cutoff_never_enables_selection(self):
        with closing(sqlite3.connect(self.db)) as db,db:
            db.execute("UPDATE stock_sectors SET updated_at='2026-10-29T10:00:00+09:00'")
        self.refresh();report=self.diagnose()
        self.assertEqual(report['sectors']['connected_stock_count'],0)
        self.assertFalse(report['am_selection']['ready_for_verified_briefing'])

    def test_malformed_manifest_rejected_without_private_exception_text(self):
        with self.assertRaisesRegex(ValueError,'invalid_manifest_scope'): self.diagnose(evidence=['private'])
        for _,proof in self.fixture.proofs('quote'): proof['units']=['private']
        report=self.diagnose()
        self.assertFalse(report['am_selection']['ready_for_verified_briefing'])
        self.assertNotIn('private',json.dumps(report))

    def test_source_change_during_adapter_phase_is_safe_missing_evidence(self):
        with patch.object(a,'adapt',side_effect=RuntimeError('DO_NOT_EXPORT_PRIVATE_PATH')):
            report=self.diagnose()
        self.assertEqual(report['am_selection']['adapter_state'],'rejected')
        self.assertFalse(report['am_selection']['ready_for_verified_briefing'])
        self.assertNotIn('DO_NOT_EXPORT',json.dumps(report))

    def test_capital_and_sector_missing_are_reported(self):
        report=self.diagnose(capital_json=None)
        self.assertFalse(report['capital']['source_present'])
        self.assertFalse(report['am_selection']['ready_for_verified_briefing'])
        for record in a.read_sources(self.db)['records']:
            if record['kind']=='sector': self.fixture.manifest['records'][a.fingerprint(record)]['verified']=False
        report=self.diagnose()
        self.assertEqual(report['sectors']['connected_stock_count'],0)
        self.assertFalse(report['am_selection']['ready_for_verified_briefing'])

    def test_output_never_contains_raw_private_fields_paths_or_original_ids(self):
        raw=json.dumps(self.diagnose(),ensure_ascii=False)
        for value in ['DO_NOT_EXPORT_PRIVATE_AUTH','DO_NOT_EXPORT_ACCOUNT',str(self.db),'run0','raw_json','token','account']:
            self.assertNotIn(value,raw)

    def test_busy_or_missing_sources_do_not_modify_or_create_them(self):
        missing=self.root/'nonexistent.db'
        report=d.diagnose(missing,trade_date='2026-10-29',cutoff_at=CUTOFF,environment='mock',synthetic=True)
        self.assertFalse(missing.exists());self.assertFalse(report['am_selection']['ready_for_verified_briefing'])
        before=self.db.read_bytes();wal=Path(str(self.db)+'-wal');wal.write_bytes(b'synthetic-active-sidecar')
        try:
            report=self.diagnose()
            self.assertNotIn('ranking',report['sources'])
            self.assertEqual(wal.read_bytes(),b'synthetic-active-sidecar')
            self.assertEqual(self.db.read_bytes(),before)
        finally: wal.unlink()

    def test_read_only_json_summary_and_no_api_calls(self):
        before=self.db.read_bytes();capital_before=self.fixture.capital.read_bytes()
        from kiwoom_collector import d0_store, telegram_briefing, openai_tts
        with patch('socket.socket.connect',side_effect=AssertionError('network')), \
             patch.object(d0_store,'save_record',side_effect=AssertionError('D0')), \
             patch.object(telegram_briefing,'deliver',side_effect=AssertionError('Telegram')), \
             patch.object(openai_tts.OpenAITTS,'synthesize',side_effect=AssertionError('TTS')), \
             patch.object(am,'PROJECT',self.root),patch.object(am,'REPORTS',self.root/'reports'):
            report=self.diagnose();paths=d.write_reports(report,'synthetic-c8')
            self.assertEqual(json.loads(paths[0].read_text(encoding='utf-8')),report)
            self.assertEqual(paths[1].read_text(encoding='utf-8'),report['summary'])
            with self.assertRaises(ValueError): d.write_reports(report,'synthetic-c8')
        self.assertEqual(self.db.read_bytes(),before);self.assertEqual(self.fixture.capital.read_bytes(),capital_before)

    def test_cli_exit_codes_and_generic_private_error(self):
        manifest=self.root/'manifest.json';manifest.write_text(json.dumps(self.fixture.manifest),encoding='utf-8')
        args=['--ranking-db',str(self.db),'--date','2026-10-29','--cutoff',CUTOFF,'--environment','mock','--synthetic']
        with patch.object(am,'PROJECT',self.root),patch.object(am,'REPORTS',self.root/'reports'):
            with patch('sys.stdout',new_callable=io.StringIO) as output:
                self.assertEqual(d.main(args+['--name','missing','--format','json']),2)
                self.assertFalse(json.loads(output.getvalue())['am_selection']['ready_for_verified_briefing'])
            with patch('sys.stdout',new_callable=io.StringIO) as output:
                self.assertEqual(d.main(args+['--capital-json',str(self.fixture.capital),'--evidence',str(manifest),'--name','complete']),0)
                self.assertIn('선정 검증: 가능',output.getvalue())
            with patch('sys.stdout',new_callable=io.StringIO) as output:
                self.assertEqual(d.main(args+['--name','../DO_NOT_EXPORT_PRIVATE_AUTH']),1)
                self.assertNotIn('DO_NOT_EXPORT',output.getvalue())
