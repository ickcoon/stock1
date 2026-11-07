"""2026-11-05 C15-11 preview validation using read-only sources, no delivery."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from kiwoom_collector import c15_preview, c15_briefing as briefing
from test_c15_leadership_actual import CONFIG, ROOT
from test_c15_briefing import DISPLAY, TURNOVER


class ActualBriefingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not CONFIG.is_file():raise unittest.SkipTest('local config unavailable')
        config=json.loads(CONFIG.read_text(encoding='utf8'))
        if not Path(config['runtime_root']).is_dir():raise unittest.SkipTest('saved runtime unavailable')
        patch_file=ROOT/'diagnostics/c15-10b-20261105/runtime-schedule-proposal.diff'
        cls.patch_hash=hashlib.sha256(patch_file.read_bytes()).hexdigest() if patch_file.exists() else None
        connect=sqlite3.connect
        def readonly(path,*args,**kwargs):
            if '?mode=ro' not in str(path) or kwargs.get('uri') is not True:raise AssertionError('read-only URI required')
            return connect(path,*args,**kwargs)
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')),patch('sqlite3.connect',side_effect=readonly),patch.object(briefing.telegram,'deliver',side_effect=AssertionError('delivery forbidden')):
            cls.sources={slot:c15_preview.generate(config,day='2026-11-05',slot=slot) for slot in c15_preview.SLOTS}
            original=deepcopy(cls.sources)
            cls.results={slot:briefing.prepare(r,DISPLAY,TURNOVER) for slot,r in cls.sources.items()}
            if cls.sources!=original:raise AssertionError('source analysis mutated')

    def test_three_previews_are_compact_without_diagnostic_lists_or_paths(self):
        for r in self.results.values():
            self.assertEqual(len(r['telegram_preview_messages']),1)
            self.assertLessEqual(len(r['text'].encode('utf-16-le'))//2,4096)
            self.assertNotIn('C:\\',r['text']);self.assertNotIn('미분류:',r['text'])
            self.assertIn('잠정 관측 후보',r['text']);self.assertFalse(r['candidate_confirmed'])
            self.assertEqual(r['api_calls'],0);self.assertFalse(r['telegram_sent'])

    def test_cached_0945_prices_and_turnover_keep_0940_clock(self):
        r=self.results['09:45'];self.assertIn('0186 기준 09:40 (5분 전 대체)',r['text'])
        self.assertIn('등락률·대금 09:40 기준',r['text']);self.assertIn('신규 매수세 해석 금지',r['text'])
        m=r['diagnostics']['stock_measurements']['128940']
        self.assertEqual(m['change']['raw_value'],'+9.68');self.assertEqual(m['change']['observed_at'][11:16],'09:40')
        self.assertEqual(m['turnover']['request_started_at'][11:16],'09:40')
        self.assertEqual(m['turnover']['eok'],'365.04')

    def test_latest_available_capital_is_1103_not_later_collected_1104(self):
        for r in self.results.values():
            for sector in r['analysis']['sectors']:
                for s in sector['leaders']:
                    cap=r['diagnostics']['stock_measurements'][s['stock_code']]['capital']
                    self.assertEqual(cap['basis_date'],'2026-11-03')
                    self.assertTrue(cap['raw']['raw_fields_match'])
                    self.assertFalse(cap['intraday_cap_verified'])
            self.assertIn('시총 11/03 종가 참고',r['text'])
        hanmi=self.results['09:40']['diagnostics']['stock_measurements']['128940']['capital']
        self.assertEqual(hanmi['won'],'5290939283000')
        self.assertNotEqual(hanmi['won'],'5425454688500')

    def test_existing_floor_removes_daemyung_and_daedong_and_reuses_weights(self):
        for r in self.results.values():
            leaders={s['stock_code'] for sector in r['analysis']['sectors'] for s in sector['leaders']}
            self.assertNotIn('389260',leaders);self.assertNotIn('008830',leaders)
            for code in ('389260','008830'):
                cap=r['diagnostics']['stock_measurements'][code]['capital']
                self.assertLess(Decimal(cap['won']),Decimal('300000000000'))
            self.assertTrue(r['diagnostics']['score_policy_unchanged'])
            self.assertEqual(r['analysis']['policy'],r['diagnostics']['source_report']['morning_leadership']['analysis']['policy'])

    def test_product_exclusion_has_no_fabricated_actual_gain(self):
        for slot,total,mapped in [('09:40',103,50),('09:45',103,50),('09:50',105,52)]:
            r=self.results[slot];c=r['diagnostics']['comparison']
            self.assertEqual(c['before']['excluded_products'],0);self.assertEqual(c['after_product']['excluded_products'],0)
            self.assertEqual(c['after_product']['raw_observed'],total)
            self.assertEqual(c['after_product']['type_pending'],38)
            self.assertEqual(c['after_product']['mapping_coverage']['theme_linked'],mapped)
            m=r['diagnostics']['stock_measurements']['069500']['product']
            self.assertTrue(m['name_review_only']);self.assertFalse(m['excluded']);self.assertEqual(m['status'],'pending')
            self.assertEqual([(s['name'],s['score']) for s in c['before_top3']],[(s['name'],s['score']) for s in c['after_product_top3']])

    def test_final_rankings_reflect_capital_not_new_scoring_weights(self):
        expected={'09:40':['제약바이오','신재생에너지','은행'],
                  '09:45':['제약바이오','신재생에너지','은행'],
                  '09:50':['제약바이오','은행','신재생에너지']}
        for slot,names in expected.items():
            self.assertEqual([s['name'] for s in self.results[slot]['analysis']['sectors']],names)

    def test_diagnostics_keep_raw_fields_dates_paths_and_amount_gate(self):
        for r in self.results.values():
            m=r['diagnostics']['stock_measurements']['128940']
            self.assertEqual(m['capital']['source_field'],'MKTCAP')
            self.assertTrue(m['capital']['source_path']);self.assertEqual(m['turnover']['source_field'],'trde_prica')
            self.assertFalse(m['turnover']['cumulative_period_verified']);self.assertFalse(m['turnover']['amount_score_enabled'])
            self.assertEqual(r['diagnostics']['ka10001_cache']['cache_rows'],0)

    def test_unapplied_schedule_patch_is_unchanged(self):
        p=ROOT/'diagnostics/c15-10b-20261105/runtime-schedule-proposal.diff'
        if self.patch_hash is None:self.skipTest('previous proposal artifact unavailable')
        self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),self.patch_hash)


if __name__=='__main__':unittest.main()
