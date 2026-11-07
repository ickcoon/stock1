"""Synthetic C15-10B bounded substitution; no market requests."""
from copy import deepcopy
from datetime import timedelta
import json
import unittest

from kiwoom_collector import c15_turnover_selection as policy, c12_latest, c15_preview
from kiwoom_collector.am_time_policy import instant
from test_c12_latest import snapshot,stamp


def turn(clock='09:40:00',end='09:40:01',identifier='old',**updates):
    s=snapshot('0186',clock=clock,end=end,identifier=identifier)
    raw=dict(stk_cd='000001',stk_nm='Synthetic',now_rank='1',cur_prc='+100',flu_rt='+2',now_trde_qty='10',trde_prica='123')
    s['rows'][0]['raw_json']=json.dumps(raw)
    s.update(updates);s['snapshot_sha256']=c12_latest.digest({k:v for k,v in s.items() if k!='snapshot_sha256'})
    return s


def choose(rows,slot='09:45'):
    reference=stamp(slot+':00')
    return policy.select(rows,reference_at=reference,deadline_at=(instant(reference)+timedelta(seconds=45)).isoformat())


class TurnoverSelectionTests(unittest.TestCase):
    def test_0945_uses_0940_with_separate_clock(self):
        result=choose([turn()])
        self.assertEqual(result['freshness']['state'],'fallback_within_10m')
        self.assertEqual(result['freshness']['slot_lag_minutes'],5)
        self.assertEqual(result['freshness']['age_seconds_at_reference'],300)
        self.assertIn('최신 실시간 아님',policy.format_source(result['freshness']))

    def test_0950_inclusive_ten_minutes_and_expired_boundary(self):
        self.assertEqual(choose([turn()],'09:50')['freshness']['age_seconds_at_reference'],600)
        old=turn(clock='09:39:59',end='09:40:00')
        self.assertIsNone(choose([old],'09:50')['snapshot'])

    def test_other_day_future_or_unfinished_storage_rejected(self):
        yesterday=turn();yesterday['collection_started_at']='2026-11-02T09:40:00+09:00'
        yesterday['snapshot_sha256']=c12_latest.digest({k:v for k,v in yesterday.items() if k!='snapshot_sha256'})
        bad=[yesterday,turn(clock='09:50:00',end='09:50:01'),
             turn(clock='09:44:00',end='09:44:01',db_committed_by=stamp('09:45:01')),
             turn(db_committed_by=None),turn(status='error')]
        for s in bad:self.assertIsNone(choose([s])['snapshot'])

    def test_exact_slot_preferred_even_when_past_is_more_recently_committed(self):
        exact=turn('09:45:00','09:45:01','exact')
        result=choose([turn(),exact])
        self.assertEqual(result['snapshot']['id'],'exact');self.assertFalse(result['freshness']['is_fallback'])

    def test_latest_collection_not_latest_commit(self):
        a=turn('09:42:00','09:42:01','a',db_committed_by=stamp('09:44:59'))
        b=turn('09:44:00','09:44:01','b')
        self.assertEqual(choose([a,b])['snapshot']['id'],'b')

    def test_invalid_exact_can_fall_back_but_invalid_rows_cannot(self):
        bad=turn('09:45:00','09:45:01','bad',status='invalid')
        self.assertEqual(choose([turn(),bad])['snapshot']['id'],'old')
        bad=turn();bad['rows'][0]['raw_json']='{}'
        bad['snapshot_sha256']=c12_latest.digest({k:v for k,v in bad.items() if k!='snapshot_sha256'})
        self.assertIsNone(choose([bad])['snapshot'])

    def test_partial_valid_page_preserved_but_bad_profile_and_fingerprint_rejected(self):
        s=turn(original_status='partial')
        self.assertTrue(choose([s])['freshness']['partial'])
        for bad in [turn(environment='mock'),turn(profile=dict(mrkt_tp='000',mang_stk_incls='0',stex_tp='3'))]:
            self.assertIsNone(choose([bad])['snapshot'])
        s['rows'][0]['stock_code']='999999'
        self.assertIsNone(choose([s])['snapshot'])

    def test_ambiguous_latest_does_not_choose_arbitrary_id(self):
        self.assertEqual(choose([turn(identifier='a'),turn(identifier='b')])['freshness']['state'],'ambiguous')

    def test_selector_does_not_mutate_sources_or_accept_longer_limits(self):
        rows=[turn()];before=deepcopy(rows);choose(rows)
        self.assertEqual(rows,before)
        with self.assertRaises(ValueError):
            policy.select(rows,reference_at=stamp('09:45:00'),deadline_at=stamp('09:45:45'),max_age_seconds=601)

    def test_ranking_within_scheduler_grace_is_not_dropped(self):
        s=snapshot(clock='09:45:02',end='09:45:03',scheduled_at=stamp('09:45:00'))
        paired,ranks,turns=c15_preview.exact_slot([s],'2026-11-03','09:45')
        self.assertEqual(len(ranks),1);self.assertEqual([r['id'] for r in paired],[s['id']])


if __name__=='__main__':unittest.main()
