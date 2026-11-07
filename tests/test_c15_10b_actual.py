"""C15-10B actual saved slots; source databases are always read-only."""
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from kiwoom_collector import c15_preview, c15_flow, c15_turnover_selection
from test_c15_leadership_actual import CONFIG, ROOT
from test_c15_flow import CONFIG as FLOW_CONFIG, sector
from test_c15_turnover_audit import CONTRACT


class ActualFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not CONFIG.is_file():raise unittest.SkipTest('local config unavailable')
        config=json.loads(CONFIG.read_text(encoding='utf-8-sig'))
        if not Path(config['runtime_root']).is_dir():raise unittest.SkipTest('saved runtime unavailable')
        config['turnover_fallback']=True
        connect=sqlite3.connect
        def readonly(path,*args,**kwargs):
            if '?mode=ro' not in str(path) or kwargs.get('uri') is not True:raise AssertionError('read-only URI required')
            return connect(path,*args,**kwargs)
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')),patch('sqlite3.connect',side_effect=readonly):
            cls.results={slot:c15_preview.generate(config,day='2026-11-05',slot=slot) for slot in c15_preview.SLOTS}
            cls.flow=c15_flow.attach_unit_audits(c15_flow.analyze(list(cls.results.values()),FLOW_CONFIG),list(cls.results.values()),CONTRACT)

    def test_actual_exact_precedence_and_requested_vs_reference_rows(self):
        for slot in ('09:40','09:50'):
            r=self.results[slot]
            self.assertEqual(r['turnover_freshness']['state'],'exact_slot')
            self.assertEqual(r['turnover_freshness']['request_started_at'][11:16],slot)
        r=self.results['09:45']
        self.assertEqual(r['requested_slot_raw_counts']['0186'],0)
        self.assertEqual(r['raw_counts']['0186'],100)
        self.assertEqual(Path(r['paths']['turnover']).stem,'0940')
        self.assertEqual(Path(r['paths']['turnover_requested']).stem,'0945')

    def test_actual_0945_fallback_provenance_and_no_future_prices(self):
        r=self.results['09:45'];m=r['turnover_freshness']
        self.assertTrue(m['is_fallback']);self.assertEqual(m['slot_lag_minutes'],5)
        self.assertGreater(m['age_seconds_at_reference'],298);self.assertLessEqual(m['age_seconds_at_reference'],300)
        self.assertTrue(m['partial'])
        self.assertIn('최신 실시간 아님',r['text']);self.assertIn('09:40',r['telegram_preview_text'])
        stock=next(s for s in r['morning_leadership']['stocks'] if s['stock_code']=='005930')
        self.assertEqual(stock['raw_change'],'-5.39')
        self.assertNotEqual(stock['raw_change'],'-4.93')

    def test_forced_missing_0950_can_reuse_actual_0940_within_ten_minutes(self):
        # Deliberately omit the real 09:50 snapshot only in this selector test.
        old=self.results['09:40']['analysis']['snapshot_selection']['selected']['0186']['snapshot']
        choice=c15_turnover_selection.select([old],reference_at='2026-11-05T09:50:00+09:00',deadline_at='2026-11-05T09:50:45+09:00')
        self.assertTrue(choice['freshness']['is_fallback'])
        self.assertEqual(choice['freshness']['slot_lag_minutes'],10)
        self.assertLessEqual(choice['freshness']['age_seconds_at_reference'],600)

    def test_score_policy_and_exact_slot_selections_unchanged(self):
        for slot in ('09:40','09:50'):
            old=json.loads((ROOT/'diagnostics/c15-9-20261105'/slot.replace(':','')/'preview.json').read_text(encoding='utf8'))
            before=old['morning_leadership']['analysis'];after=self.results[slot]['morning_leadership']['analysis']
            self.assertEqual(before['policy'],after['policy'])
            self.assertEqual([(s['name'],s['score']) for s in before['sectors']],[(s['name'],s['score']) for s in after['sectors']])
        before=self.results['09:40']['morning_leadership']['analysis']['sectors'][0]
        after=self.results['09:45']['morning_leadership']['analysis']['sectors'][0]
        self.assertEqual(before['components']['strength'],after['components']['strength'])

    def test_cached_preview_score_does_not_become_a_new_flow_measurement(self):
        row=self.flow['slots']['09:45']['제약바이오']
        self.assertIsNotNone(row['score']);self.assertTrue(row['source_presence']['0186'])
        self.assertFalse(row['fresh_source_presence']['0186'])
        for pair in self.flow['adjacent_comparisons']:
            s=sector(pair,'제약바이오')
            self.assertEqual(s['state'],'held');self.assertEqual(s['common_strength_grade'],'C')
            self.assertEqual(s['reason'],'cached_0186_not_new_measurement')
            self.assertEqual(s['common_attention_grade'],'A')
        self.assertEqual(self.flow['available_endpoint_comparison']['from_slot'],'09:40')
        self.assertEqual(self.flow['available_endpoint_comparison']['to_slot'],'09:50')

    def test_cached_unit_audit_uses_original_database_and_does_not_enable_amounts(self):
        audit=self.flow['turnover_unit_audits']['09:45']
        self.assertTrue(audit['storage_consistent']);self.assertTrue(audit['reference_freshness']['is_fallback'])
        self.assertFalse(audit['amount_score_enabled'])
        self.assertEqual(audit['snapshot_sha256'],self.flow['turnover_unit_audits']['09:40']['snapshot_sha256'])

    def test_no_delivery_and_message_limits(self):
        self.assertFalse(self.flow['telegram_sent']);self.assertEqual(self.flow['api_calls'],0)
        self.assertTrue(all(len(t.encode('utf-16-le'))//2<=4096 for t in self.flow['telegram_preview_messages']))
        self.assertEqual(''.join(self.flow['telegram_preview_messages']),self.flow['text'])


if __name__=='__main__':unittest.main()
