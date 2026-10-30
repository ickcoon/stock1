"""C10 timing/master -> C7 -> C6 -> text, entirely synthetic and local."""
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_collector import am_time_policy as tp, am_briefing as briefing
from kiwoom_collector import am_input_adapter as adapter, am_data_diagnostic as diagnostic
from kiwoom_collector import master_data as master, market_cap as mc
import test_am_briefing as fixture
import test_am_data_diagnostic as db_fixture

POLICY=tp.ObservationPolicy().document()
REF='2026-10-29T09:50:00+09:00'
DECISION='2026-10-29T09:50:45+09:00'
EARLY='2026-10-29T08:00:00+09:00'


class AMWindowTests(unittest.TestCase):
    def doc(self):
        document=deepcopy(fixture.FIXTURE)
        document['observation_policy']=deepcopy(POLICY)
        for ranking in document['rankings']:
            ranking['received_at']=(tp.instant(ranking['observed_at'])+timedelta(seconds=12)).isoformat()
        for security in document['securities'].values():
            for key in ('names','price','change_pct','turnover','market_cap','liquidity'):
                for value in security.get(key,[]):
                    value['received_at']=(tp.instant(value['observed_at'])+timedelta(seconds=12)).isoformat()
        return document

    def test_twelve_second_receipt_is_accepted_without_rewriting_reference_or_receipt(self):
        document=self.doc(); report=briefing.analyze(document,generated_at=DECISION)
        self.assertEqual(len(report['sectors']),3)
        self.assertEqual(report['selection_cutoff_at'],REF)
        self.assertEqual(report['decision_deadline_at'],DECISION)
        quote=report['candidates'][0]['metrics']['change_pct']
        self.assertEqual(quote['received_at'],'2026-10-29T09:50:12+09:00')
        text=briefing.format_telegram(report)
        self.assertIn('09:50:45',text);self.assertIn('최종 탐색',text)

    def test_grace_boundary_and_invalid_config(self):
        policy=tp.ObservationPolicy()
        self.assertTrue(policy.near(REF,DECISION,REF))
        self.assertFalse(policy.near(REF,'2026-10-29T09:50:46+09:00',REF))
        for config in ({'receipt_grace_seconds':301},{'capital_ttl_seconds':0},{'observation_after_seconds':True}):
            with self.assertRaises(ValueError):tp.ObservationPolicy(**config)
        with self.assertRaises(ValueError):tp.from_document({'observation_policy':{'receipt_grace_seconds':45}})

    def test_earlier_minute_in_window_accepted_but_stale_previous_day_and_environment_not_joined(self):
        policy=tp.ObservationPolicy()
        self.assertTrue(policy.near('2026-10-29T09:49:00+09:00','2026-10-29T09:50:12+09:00',REF))
        self.assertFalse(policy.near('2026-10-29T09:48:00+09:00','2026-10-29T09:50:12+09:00',REF))
        self.assertFalse(policy.available('2026-10-28T09:50:00+09:00','2026-10-29T09:50:12+09:00',REF))
        document=self.doc();document['securities']['000001']['change_pct'][-1]['environment']='real'
        report=briefing.analyze(document,generated_at=DECISION)
        item=next(c for c in report['candidates'] if c['stock_code']=='000001')
        self.assertIn('change_pct_unverified',item['missing_evidence'])

    def test_generation_before_decision_deadline_rejected(self):
        with self.assertRaisesRegex(ValueError,'generation_before_cutoff'):
            briefing.analyze(self.doc(),generated_at=REF)

    def test_preliminary_three_slots_at_0940_supported_without_final_claim(self):
        document=self.doc();document['selection_cutoff_at']='2026-10-29T09:40:00+09:00'
        for security in document['securities'].values():
            for key in ('names','price','change_pct','turnover','market_cap','liquidity'):
                for value in security.get(key,[]):
                    if value['observed_at'][11:16]=='09:50':
                        value['observed_at']=value['observed_at'].replace('09:50','09:40')
                        value['received_at']=value['received_at'].replace('09:50','09:40')
        report=briefing.analyze(document,generated_at='2026-10-29T09:40:45+09:00')
        self.assertEqual(report['analysis']['completeness']['accepted_slots'],['09:20','09:30','09:40'])
        self.assertEqual(report['observation_phase'],'preliminary')
        self.assertGreater(len(report['sectors']),0)
        self.assertIn('예비 탐색',briefing.format_telegram(report))

    def test_old_reports_keep_strict_policy(self):
        document=self.doc();document.pop('observation_policy')
        report=briefing.analyze(document,generated_at=DECISION)
        self.assertEqual(report['sectors'],[])
        self.assertNotIn('decision_deadline_at',report)


class MasterAdapterIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=db_fixture.AMDataDiagnosticTests('test_full_synthetic_one_pass_checks_readiness_and_separate_times')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.db=self.fixture.db
        self.root=self.fixture.root
        self.patch=patch.multiple(master,MASTER_ROOT=self.root/'master',PROJECT=self.root)
        self.patch.start();self.addCleanup(self.patch.stop)
        self.master=master.create_master('integration',synthetic=True,at=EARLY)
        with closing(sqlite3.connect(self.db)) as db,db:
            db.execute('UPDATE quote_batches SET test_mode=0')
            db.execute('UPDATE quote_observations SET test_mode=0')
            for row in db.execute('SELECT rowid,request_finished_at FROM quote_observations').fetchall():
                end=(tp.instant(row[1])+timedelta(seconds=12)).isoformat()
                db.execute('UPDATE quote_observations SET request_finished_at=? WHERE rowid=?',(end,row[0]))
        self.fixture.refresh()
        source=adapter.read_sources(self.db)
        for record in source['records']:
            proof=self.fixture.fixture.manifest['records'][adapter.fingerprint(record)]
            if record['kind']=='quote':
                proof['observed_at']=tp.instant(record['date'][:4]+'-'+record['date'][4:6]+'-'+record['date'][6:]+'T'+record['tm'][:2]+':'+record['tm'][2:4]+':'+record['tm'][4:]+'+09:00').isoformat()
                proof['received_at']=record['request_finished_at']
            elif record['kind']=='ranking':
                proof['received_at']=(tp.instant(record['collected_at'])+timedelta(seconds=5)).isoformat()
            elif record['kind']=='sector':
                del self.fixture.fixture.manifest['records'][adapter.fingerprint(record)]
        with master.MasterStore(self.master) as store:
            sector_ids={}
            for stock_code,security in fixture.FIXTURE['securities'].items():
                store.put_stock(stock_code,security['names'][0]['value'],'KOSDAQ','listed',source='synthetic_listing',
                    updated_at=EARLY,registered_at=EARLY,verified=True)
                sector=security['sectors'][0]['name']
                if sector not in sector_ids:
                    existing=store.db.execute("SELECT sector_id FROM sector_master WHERE sector_name=? AND kind='industry'",(sector,)).fetchone()
                    sid=existing[0] if existing else 'synthetic_'+str(len(sector_ids))
                    sector_ids[sector]=sid
                    if not existing:
                        store.put_sector(sid,sector,kind='industry',source='synthetic_taxonomy',updated_at=EARLY)
                store.put_tag(stock_code,sector_ids[sector],source='synthetic_review',evidence='synthetic_sector_review',
                    updated_at=EARLY,registered_at=EARLY,valid_from=EARLY,is_primary=True,status='verified')
                body=dict(return_code=0,stk_cd=stock_code,stk_nm=security['names'][0]['value'],mac='10000',flo_stk='100',cur_prc='+100')
                received='2026-10-29T09:20:01+09:00'
                parsed=mc.parse_response(body,requested_code=stock_code,environment='mock',request_started_at=received,response_received_at=received)
                store.put_capital(body,requested_code=stock_code,environment='mock',request_started_at=received,
                    response_received_at=received,registered_at=received,source='synthetic_ka10001',proof=dict(
                        for_record=parsed['fingerprint'],stock_code=stock_code,environment='mock',verified=True,
                        unit='hundred_million_KRW',unit_runtime_verified=True,market_cap_basis_verified=True,
                        observed_at=received,trade_date='2026-10-29',evidence='SYNTHETIC cap review'))

    def adapt(self,**kwargs):
        args=dict(trade_date='2026-10-29',cutoff_at=REF,environment='mock',synthetic=True,
            evidence=self.fixture.fixture.manifest,master_db=self.master,observation_policy=deepcopy(POLICY))
        args.update(kwargs);return adapter.adapt(self.db,**args)

    def test_master_c7_c6_and_text_support_normal_collection_mode_and_receipt_delay(self):
        document=self.adapt();report=briefing.analyze(document,generated_at=DECISION)
        self.assertEqual(len(report['sectors']),3)
        capitals=[c['metrics']['market_cap'] for c in report['candidates'] if c['metrics']['market_cap']]
        self.assertEqual(len(capitals),9)
        self.assertTrue(all(c['observed_at']=='2026-10-29T09:20:01+09:00' for c in capitals))
        self.assertEqual(report['policy']['absolute_am_turnover_min_won'],None)
        self.assertIn('합성 테스트 데이터',briefing.format_telegram(report))
        self.assertTrue(document['master_diagnostics']['hash_unchanged'])
        self.assertEqual(document['master_diagnostics']['records'][0]['sector_source_updated_at'],EARLY)

    def test_c8_diagnostic_uses_master_and_window_without_rewriting_raw_times(self):
        report=diagnostic.diagnose(self.db,trade_date='2026-10-29',cutoff_at=REF,environment='mock',synthetic=True,
            evidence=self.fixture.fixture.manifest,master_db=self.master,observation_policy=deepcopy(POLICY))
        self.assertTrue(report['am_selection']['ready_for_verified_briefing'],report['missing_evidence'])
        self.assertEqual(report['am_selection']['engine_sector_count'],3)
        self.assertEqual(report['capital']['connected_stock_count'],9)
        self.assertTrue(all(q['verified'] for q in report['quotes']))
        self.assertEqual(report['decision_deadline_at'],DECISION)

    def test_unverified_sector_cache_and_source_scope_cannot_force_candidates(self):
        with master.MasterStore(self.master) as store:
            store.db.execute("UPDATE stock_sector SET status='proposed'")
        report=briefing.analyze(self.adapt(),generated_at=DECISION)
        self.assertEqual(report['sectors'],[])
        with self.assertRaises(ValueError):self.adapt(synthetic=False)

    def test_missing_evidence_not_replaced_by_master(self):
        report=briefing.analyze(self.adapt(evidence=None),generated_at=DECISION)
        self.assertEqual(report['sectors'],[])
        self.assertEqual(report['candidates'],[])

    def test_master_delisted_stock_cannot_retain_legacy_eligibility(self):
        with master.MasterStore(self.master) as store:
            store.put_stock('000001','합성상폐','KOSDAQ','delisted',source='synthetic_delisting',
                updated_at='2026-10-29T09:30:00+09:00',registered_at='2026-10-29T09:30:00+09:00',verified=True)
        report=briefing.analyze(self.adapt(),generated_at=DECISION)
        row=next(c for c in report['candidates'] if c['stock_code']=='000001')
        self.assertEqual(row['status'],'held')
        self.assertIsNone(row['sector']);self.assertIsNone(row['metrics']['market_cap'])
