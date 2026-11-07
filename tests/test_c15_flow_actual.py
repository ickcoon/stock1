"""2026-11-05 saved data: read-only, network forbidden, no transmission."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from kiwoom_collector import c15_flow as flow
import test_c15_leadership_actual as previous
from test_c15_flow import CONFIG, sector
from test_c15_turnover_audit import CONTRACT


class ActualFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not previous.CONFIG.is_file():raise unittest.SkipTest('local C15 config unavailable')
        previous.ActualLeadershipTests.setUpClass.__func__(cls)
        cls.inputs=list(cls.results.values());cls.original=deepcopy(cls.inputs)
        connect=sqlite3.connect
        def readonly(path,*args,**kwargs):
            if '?mode=ro' not in str(path) or kwargs.get('uri') is not True:raise AssertionError('read-only URI required')
            return connect(path,*args,**kwargs)
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')),patch('sqlite3.connect',side_effect=readonly):
            cls.flow=flow.attach_unit_audits(flow.analyze(cls.inputs,CONFIG),cls.inputs,CONTRACT)

    def test_original_scores_inputs_and_weights_unchanged(self):
        self.assertEqual(self.inputs,self.original)
        for slot,report in self.results.items():
            for s in report['morning_leadership']['analysis']['ranked_sectors']:
                row=self.flow['slots'][slot][s['name']]
                self.assertEqual((row['score'],row['rank'],row['rising_count']),(s['score'],s['rank'],s['rising_count']))
        self.assertEqual(self.flow['score_policy'],self.inputs[0]['morning_leadership']['analysis']['policy'])

    def test_missing_0945_is_not_backfilled_or_zero(self):
        self.assertEqual(self.flow['skipped_strength_slots'],['09:45'])
        self.assertTrue(all(r['rising_count'] is None and r['score'] is None for r in self.flow['slots']['09:45'].values()))
        self.assertTrue(all(s['state']=='held' for p in self.flow['adjacent_comparisons'] for s in p['sectors']))
        self.assertNotIn('은행',self.flow['slots']['09:45'])

    def test_bank_top3_entry_is_added_observation_not_increasing_common_strength(self):
        before,after=self.flow['slots']['09:40']['은행'],self.flow['slots']['09:50']['은행']
        self.assertEqual((before['rank'],before['rising_count'],before['observed_count']),(4,3,3))
        self.assertEqual((after['rank'],after['rising_count'],after['observed_count']),(3,4,4))
        s=sector(self.flow['available_endpoint_comparison'],'은행')
        self.assertEqual(s['added_members'],['055550']);self.assertEqual(s['state'],'weakening')
        self.assertEqual(s['after']['rising_count'],3);self.assertEqual(s['before']['rising_count'],3)
        self.assertLess(Decimal(s['after']['strength']),Decimal(s['before']['strength']))
        self.assertEqual(s['after']['score'],'35.71')
        self.assertEqual((s['total_score_grade'],s['common_strength_grade']),('B','A'))

    def test_provisional_persistence_and_mapping_or_coverage_holds(self):
        p=self.flow['available_endpoint_comparison']
        for name in ('제약바이오','신재생에너지','로봇'):
            self.assertEqual(sector(p,name)['state'],'persistent')
            self.assertFalse(sector(p,name)['long_term_confirmed'])
        self.assertEqual(sector(p,'전선·전력설비')['state'],'held')
        self.assertEqual(sector(p,'전선·전력설비')['common_strength_grade'],'C')

    def test_attention_is_independent_of_0186_missing(self):
        for p in self.flow['adjacent_comparisons']:
            s=sector(p,'제약바이오')
            self.assertEqual(s['common_attention_grade'],'A')
            self.assertEqual(s['common_strength_grade'],'C')
        self.assertEqual(sector(self.flow['available_endpoint_comparison'],'은행')['common_attention_grade'],'C')

    def test_requested_themes_not_invented_and_non_top3_retained(self):
        self.assertEqual(self.flow['watch_sectors']['게임']['observed_labels'],[])
        self.assertEqual(self.flow['watch_sectors']['항공/여행']['observed_labels'],[])
        self.assertEqual(self.flow['watch_sectors']['재건']['observed_labels'],['일부 재건주'])
        self.assertNotIn('재건',self.flow['sectors'])
        self.assertEqual(len(self.flow['sectors']),43)

    def test_two_hundred_saved_rows_consistent_and_amount_scores_disabled(self):
        for slot in ('09:40','09:50'):
            audit=self.flow['turnover_unit_audits'][slot]
            self.assertTrue(audit['storage_consistent']);self.assertEqual(audit['checked_rows'],100)
            self.assertFalse(audit['wire_bytes_preserved']);self.assertFalse(audit['amount_score_enabled'])
            self.assertTrue(all(not r['stored_runtime_verified'] and not r['scoring_allowed'] for r in audit['rows']))
        self.assertEqual(self.flow['turnover_unit_audits']['09:45']['status'],'missing')
        comparison=self.flow['amount_score_comparison']
        self.assertEqual(comparison['before_top3'],comparison['after_documented_conversion_top3'])
        self.assertIsNone(comparison['enabled_ranking'])

    def test_utf16_preview_limits_and_no_delivery(self):
        self.assertEqual(''.join(self.flow['telegram_preview_messages']),self.flow['text'])
        self.assertTrue(all(len(s.encode('utf-16-le'))//2<=4096 for s in self.flow['telegram_preview_messages']))
        self.assertFalse(self.flow['telegram_sent']);self.assertEqual(self.flow['api_calls'],0)


if __name__=='__main__':unittest.main()
