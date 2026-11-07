"""Synthetic flow hypotheses. Quotes and classifications here are not real data."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from kiwoom_collector import c15_flow as flow
from kiwoom_collector.c12_theme_audit import analyze_themes
from test_c15_leadership import POLICY, stock

CONFIG=json.loads((Path(__file__).resolve().parents[1]/'config/c15-flow.json').read_text())


def saved(slot, definitions, *, turnover=True):
    """definitions: code, reported change, rank or None, exact theme."""
    stocks=[stock(c,change,ranking,1,product_status='common_stock',capital_status='unverified',
                  eligibility_status='pending') for c,change,ranking,theme in definitions]
    if not turnover:
        for s in stocks:s.pop('raw_change',None);s.pop('turnover_rank',None)
    tags=[dict(stock_code=c,sector_name=theme,source='explicit_synthetic') for c,change,ranking,theme in definitions]
    mapped=analyze_themes(stocks,tags,rising_policy=POLICY)
    selected={}
    for api in ('0198','0186'):
        s=dict(api=api,id=api+'-'+slot,environment='real',api_id='ka00198' if api=='0198' else 'ka10032',
               qry_tp='1',profile={'mrkt_tp':'000','mang_stk_incls':'0','stex_tp':'1'},
               collection_started_at='2026-11-05T'+slot+':00+09:00',
               request_started_at='2026-11-05T'+slot+':01+09:00',
               response_received_at='2026-11-05T'+slot+':02+09:00',
               db_committed_by='2026-11-05T'+slot+':03+09:00')
        selected[api]=dict(status='fresh' if api=='0198' or turnover else 'missing',
                           snapshot=s if api=='0198' or turnover else None)
    return dict(reference_at='2026-11-05T'+slot+':00+09:00',decision_deadline_at='2026-11-05T'+slot+':45+09:00',
        time_basis='synthetic_collection_clock',source_kind='synthetic_fixture',
        analysis=dict(snapshot_selection=dict(selected=selected)),
        morning_leadership=dict(stocks=stocks,mapping={k:v for k,v in mapped.items() if k!='rising_analysis'},
            analysis=mapped['rising_analysis'],data_status='部分' if turnover else '미확인'))


def sector(pair,name='A'):
    return next(s for s in pair['sectors'] if s['sector']==name)


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))
        self.base=[('000001','2',1,'A'),('000002','2',2,'A')]

    def test_missing_0945_is_not_zero_or_silently_replaced(self):
        reports=[saved('09:40',self.base),saved('09:45',self.base,turnover=False),saved('09:50',self.base)]
        r=flow.analyze(reports,CONFIG)
        self.assertIsNone(r['slots']['09:45']['A']['score'])
        self.assertIsNone(r['slots']['09:45']['A']['rising_count'])
        self.assertTrue(all(sector(p)['state']=='held' for p in r['adjacent_comparisons']))
        self.assertTrue(all(sector(p)['common_strength_grade']=='C' for p in r['adjacent_comparisons']))
        self.assertTrue(all(sector(p)['common_attention_grade']=='A' for p in r['adjacent_comparisons']))
        self.assertEqual(r['available_endpoint_comparison']['from_slot'],'09:40')
        self.assertEqual(r['skipped_strength_slots'],['09:45'])

    def test_persistent_on_same_companions(self):
        s=sector(flow.compare(saved('09:40',self.base),saved('09:50',self.base),CONFIG))
        self.assertEqual(s['state'],'persistent');self.assertFalse(s['long_term_confirmed'])

    def test_fixed_0198_cohort_excludes_new_symbols_without_filling_quotes(self):
        r=flow.analyze([saved('09:40',self.base),saved('09:45',self.base,turnover=False),
            saved('09:50',self.base+[('000003','9',3,'NEW')])],CONFIG)
        series=r['common_0198_series']
        self.assertEqual(series['cohort_codes'],['000001','000002'])
        self.assertTrue(all(set(rows)=={'A'} for rows in series['slots'].values()))
        self.assertFalse(series['rising_or_buying_strength_inferred'])

    def test_weakening_requires_observed_decline_not_disappearance(self):
        reduced=[('000001','1',1,'A'),('000002','-1',2,'A')]
        s=sector(flow.compare(saved('09:40',self.base),saved('09:50',reduced),CONFIG))
        self.assertEqual(s['state'],'weakening')
        missing=sector(flow.compare(saved('09:40',self.base),saved('09:50',reduced[:1]),CONFIG))
        self.assertEqual(missing['state'],'held');self.assertEqual(missing['missing_members'],['000002'])

    def test_emerging_is_strength_plus_rank_not_first_observation(self):
        before=self.base+[('000003','5',3,'B'),('000004','5',4,'B')]
        after=[('000001','7',1,'A'),('000002','7',2,'A'),('000003','4',3,'B'),('000004','4',4,'B')]
        s=sector(flow.compare(saved('09:40',before),saved('09:50',after),CONFIG))
        self.assertEqual(s['state'],'emerging')
        new=sector(flow.compare(saved('09:40',before[2:]),saved('09:50',after),CONFIG))
        self.assertEqual(new['state'],'new_observation');self.assertNotEqual(new['state'],'emerging')

    def test_added_member_inflates_total_but_not_common_strength(self):
        after=[('000001','1',1,'A'),('000002','1',2,'A'),('000003','9',3,'A')]
        s=sector(flow.compare(saved('09:40',self.base),saved('09:50',after),CONFIG))
        self.assertEqual(s['state'],'weakening');self.assertEqual(s['added_members'],['000003'])
        self.assertEqual(s['after']['rising_count'],2);self.assertEqual(s['total_score_grade'],'B')

    def test_mapping_changed_blocks_strength_classification(self):
        changed=[('000001','1',1,'B'),('000002','1',2,'A')]
        s=sector(flow.compare(saved('09:40',self.base),saved('09:50',changed),CONFIG))
        self.assertEqual(s['state'],'held');self.assertEqual(s['common_strength_grade'],'C')

    def test_future_receipt_wrong_venue_or_policy_blocks_comparison(self):
        for mutation in ('future','venue','policy'):
            a,b=saved('09:40',self.base),saved('09:50',self.base)
            turn=b['analysis']['snapshot_selection']['selected']['0186']['snapshot']
            if mutation=='future':turn['response_received_at']='2026-11-05T10:00:00+09:00'
            elif mutation=='venue':turn['profile']['stex_tp']='3'
            else:b['morning_leadership']['analysis']['policy']=dict(POLICY,strength=2)
            self.assertEqual(sector(flow.compare(a,b,CONFIG))['state'],'held')

    def test_attention_only_can_rise_while_all_stocks_fall(self):
        before=[('000001','-1',19,'A'),('000002','-2',20,'A')]
        after=[('000001','-1',1,'A'),('000002','-2',2,'A')]
        s=sector(flow.compare(saved('09:40',before),saved('09:50',after),CONFIG))
        self.assertEqual(s['common_attention_grade'],'A');self.assertEqual(s['state'],'held')
        self.assertGreater(float(s['attention_after']['attention_score']),float(s['attention_before']['attention_score']))

    def test_past_scores_and_memberships_remain_immutable(self):
        reports=[saved('09:40',self.base),saved('09:50',self.base+[('000003','9',3,'NEW')])]
        original=deepcopy(reports);r=flow.analyze(reports,CONFIG)
        self.assertEqual(reports,original);self.assertNotIn('NEW',r['slots']['09:40'])
        self.assertFalse(r['adjacent_comparisons'][0]['original_scores_rewritten'])
        self.assertEqual(r['source_kind'],'synthetic_fixture')

    def test_all_sector_scores_outside_top_three_and_message_limits(self):
        rows=[(f'{i:06}','2',i,'T'+str((i-1)//2)) for i in range(1,9)]
        r=flow.analyze([saved('09:40',rows),saved('09:50',rows)],CONFIG)
        self.assertEqual(len(r['sectors']),4);self.assertEqual(len(r['slots']['09:40']),4)
        self.assertTrue(any(s['rank']==4 for s in r['slots']['09:40'].values()))
        self.assertEqual(''.join(r['telegram_preview_messages']),r['text'])
        self.assertTrue(all(len(t.encode('utf-16-le'))//2<=4096 for t in r['telegram_preview_messages']))

    def test_duplicate_or_cross_day_inputs_rejected(self):
        a=saved('09:40',self.base)
        with self.assertRaises(ValueError):flow.analyze([a,a],CONFIG)
        b=saved('09:50',self.base);b['reference_at']=b['reference_at'].replace('11-05','11-06')
        with self.assertRaises(ValueError):flow.analyze([a,b],CONFIG)

    def test_output_outside_development_diagnostics_rejected_before_reads(self):
        with patch.object(flow.preview,'generate',side_effect=AssertionError('must not read')):
            with self.assertRaises(ValueError):
                flow.main(['--date','2026-11-05','--output-dir',str(flow.preview.PROJECT/'not-diagnostics')])


if __name__=='__main__':unittest.main()
