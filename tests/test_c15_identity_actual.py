"""Actual saved-slot C15-9 validation; reuse C15-8 read-only/network guards."""
import json
from pathlib import Path
import unittest

from kiwoom_collector.am_time_policy import instant
import test_c15_leadership_actual as previous

ROOT=Path(__file__).resolve().parents[1]


class ActualIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not previous.CONFIG.is_file(): raise unittest.SkipTest('local config unavailable')
        previous.ActualLeadershipTests.setUpClass.__func__(cls)

    def test_union_denominators_and_mapping_evidence_upgrade(self):
        for slot,linked,total,cross in [('09:40',50,103,17),('09:45',14,20,0),('09:50',52,105,15)]:
            view=self.results[slot]['morning_leadership']; c=view['mapping']['coverage']; d=view['denominator']
            self.assertEqual((c['theme_linked'],c['total'],c['official_name_mapped'],c['name_only_mapped']),(linked,total,linked,0))
            self.assertEqual((d['intersection'],d['union']),(cross,total))
            self.assertEqual(d['ranking_unique']+d['turnover_unique']-d['intersection'],total)
            self.assertTrue(all(s['code_validation']['syntax_valid'] for s in view['stocks']))

    def test_same_weights_same_sector_scores_leaders_and_unclassified(self):
        for slot in ('09:40','09:45','09:50'):
            path=ROOT/'diagnostics/c15-8-20261105'/slot.replace(':','')/'preview.json'
            if not path.exists(): self.skipTest('C15-8 baseline unavailable')
            old=json.loads(path.read_text(encoding='utf8'))['morning_leadership'];new=self.results[slot]['morning_leadership']
            self.assertEqual(old['analysis']['policy'],new['analysis']['policy'])
            def essential(v):
                return [(s['name'],s['score'],s['rising_count'],[c['stock_code'] for c in s['leaders']]) for s in v['analysis']['sectors']]
            self.assertEqual(essential(old),essential(new))
            self.assertEqual(old['mapping']['unclassified_stock_codes'],new['mapping']['unclassified_stock_codes'])

    def test_master_availability_prevents_lookahead_and_does_not_pass_cap(self):
        for result in self.results.values():
            v=result['morning_leadership'];at=instant(result['decision_deadline_at'])
            self.assertEqual(v['identity_validation']['basis_date'],'2026-11-03')
            self.assertIn('2026-11-04',v['identity_validation']['rejected_basis_dates'])
            for s in v['stocks']:
                if s['product_reference']:
                    self.assertLessEqual(instant(s['product_reference']['available_at']),at)
                self.assertEqual(s['capital_status'],'unverified')
                self.assertNotEqual(s['eligibility_status'],'eligible')

    def test_actual_preferred_unknown_types_and_no_name_inference(self):
        for slot in ('09:40','09:50'):
            v=self.results[slot]['morning_leadership']
            self.assertIn('005935',[s['stock_code'] for s in v['preferred_stocks']])
            self.assertIn('069500',[s['stock_code'] for s in v['unknown_type_stocks']])
            self.assertNotIn('069500',[s['stock_code'] for s in v['excluded_stocks']])

    def test_actual_declared_amount_unit_does_not_imply_runtime_verified(self):
        for slot in ('09:40','09:50'):
            v=self.results[slot]['morning_leadership']; t=v['turnover_validation']
            self.assertEqual(t['declared_units'],['million_KRW']);self.assertTrue(t['spec_verified'])
            self.assertFalse(t['runtime_verified']);self.assertFalse(t['amount_score_allowed'])
            self.assertEqual((t['status'],t['rows'],len(t['pages'])),('partial',100,1))
            self.assertEqual(t['pages'][0]['cont_yn'],'Y');self.assertFalse(t['pagination_complete'])
            self.assertIsNone(t['market_asof'])
        self.assertEqual(self.results['09:45']['morning_leadership']['turnover_validation']['status'],'missing')

    def test_tracking_output_includes_non_top3_and_does_not_assign_states(self):
        for result in self.results.values():
            v=result['morning_leadership']; rows=v['tracking_observations']
            self.assertEqual(len(rows),len(v['mapping']['themes']))
            self.assertGreater(len(rows),3)
            self.assertTrue(all(r['transition_state']=='not_evaluated' and not r['external_reference_used_in_score'] for r in rows))
            if result['reference_at'][11:16]=='09:45':
                self.assertTrue(all(r['score'] is None for r in rows))


if __name__=='__main__':unittest.main()
