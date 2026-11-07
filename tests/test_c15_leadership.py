"""Synthetic behavior checks; no network, collector, scheduler or DB writes."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from kiwoom_collector import c15_leadership as engine
from kiwoom_collector.c12_theme_audit import analyze_themes

POLICY = json.loads((Path(__file__).resolve().parents[1]/'config/c15-leadership.json').read_text())


def stock(code, change, ranking=1, turnover=1, **extra):
    return dict(stock_code=code, name='종목'+code, raw_change=change,
                ranking_rank=ranking, turnover_rank=turnover, **extra)


def tags(*pairs):
    return [dict(stock_code=code, sector_name=theme, source='synthetic') for code,theme in pairs]


def themes(stocks, links):
    return analyze_themes(stocks, links, rising_policy=POLICY)['rising_analysis']


def record(name, theme, code=None, day='2026-11-03'):
    return dict(raw_stock_name=name, raw_theme=theme, raw_stock_code=code, trade_date=day)


def report(stocks, turn_status='fresh'):
    rank = dict(rows=[dict(stk_cd=s['stock_code'], stk_nm=s['name'], rank=i) for i,s in enumerate(stocks,1)])
    turn = dict(original_status='partial', rows=[dict(stock_code=s['stock_code'], turnover_rank=i,
                validation_status='syntax_valid', raw_json=json.dumps(dict(stk_cd=s['stock_code'],
                stk_nm=s['name'], flu_rt=s.get('raw_change'), trde_prica='999999999'))) for i,s in enumerate(stocks,1)])
    return dict(snapshot_selection=dict(selected={'0198':dict(status='fresh', snapshot=rank),
                '0186':dict(status=turn_status,snapshot=turn)}), candidates=[], candidate_filter=dict(decisions={}))


class LeadershipTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('network forbidden')))

    def test_multiple_risers_beat_single_extreme_spike(self):
        result=themes([stock('000001','99'),stock('000002','1'),stock('000003','2')],
                      tags(('000001','단독'),('000002','동반'),('000003','동반')))
        self.assertEqual([t['name'] for t in result['sectors']],['동반'])
        self.assertEqual(result['held_sectors'][0]['confidence'],'low_single_stock')

    def test_negative_attention_never_contributes_or_leads(self):
        result=themes([stock('000001','-20'),stock('000002','1',20),stock('000003','2',19)],
                      tags(('000001','동반'),('000002','동반'),('000003','동반')))
        s=result['sectors'][0]
        self.assertEqual(s['rising_count'],2)
        self.assertEqual(s['observed_count'],3)
        self.assertNotIn('000001',[c['stock_code'] for c in s['leaders']])
        self.assertEqual(themes([stock('000001','-1')],tags(('000001','하락')))['sectors'],[])

    def test_duplicate_tags_and_multitheme_counts(self):
        stocks=[stock('000001','4'),stock('000002','2'),stock('000003','3')]
        links=tags(('000001','A'),('000002','A'),('000001','B'),('000003','B'))
        a=themes(stocks,links); b=themes(stocks,links+links)
        self.assertEqual(a,b)
        self.assertTrue(all(s['rising_count']==2 for s in a['sectors']))
        self.assertTrue(all(s['exclusive_rising_count']==1 for s in a['sectors']))
        self.assertTrue(all(s['multi_theme_stock_codes']==['000001'] for s in a['sectors']))
        self.assertEqual(a['sectors'][0]['components']['cross'],'1.5')

    def test_unclassified_stays_unclassified(self):
        result=analyze_themes([stock('000001','5')],[],rising_policy=POLICY)
        self.assertEqual(result['unclassified_stock_codes'],['000001'])
        self.assertEqual(result['rising_analysis']['sectors'],[])

    def test_code_priority_no_conflicting_name_fallback(self):
        stocks=[stock('000001','1'),stock('000002','2')]
        links,matches=engine.map_themes(stocks,[record(stocks[1]['name'],'코드우선','000001'),
            record(stocks[0]['name'],'충돌','999999'), record(stocks[1]['name'],'이름')],'2026-11-05')
        self.assertEqual({(t['stock_code'],t['sector_name']) for t in links},{('000001','코드우선'),('000002','이름')})
        self.assertEqual(matches['000001'],{'code'})

    def test_ambiguous_name_and_future_themes_not_mapped(self):
        a=stock('000001','1'); b=stock('000002','2'); b['name']=a['name']
        self.assertEqual(engine.map_themes([a,b],[record(a['name'],'중복')],'2026-11-05')[0],[])
        self.assertEqual(engine.map_themes([a],[record(a['name'],'미래',day='2026-11-06')],'2026-11-05')[0],[])

    def test_missing_or_stale_0186_never_uses_retained_rows(self):
        stocks=[stock('000001','1'),stock('000002','2')]
        records=[record(s['name'],'동반') for s in stocks]
        for state in ('missing','stale'):
            result=engine.analyze(report(stocks,state),records,{},day='2026-11-05',policy=POLICY)
            self.assertEqual(result['analysis']['sectors'],[])
            self.assertEqual(result['data_status'],'미확인')

    def test_full_saved_page_and_partial_scope(self):
        stocks=[stock(f'{i:06}', '1') for i in range(1,51)]
        result=engine.analyze(report(stocks),[record(s['name'],'동반') for s in stocks],{},day='2026-11-05',policy=POLICY)
        self.assertEqual(result['analysis']['sectors'][0]['rising_count'],50)
        self.assertEqual(result['data_status'],'부분')

    def test_explicit_product_exclusion_unknown_not_guessed(self):
        stocks=[stock('000001','2'),stock('000002','3'),stock('000003','4')]
        products={'000001':dict(product_type='ETF'), '000002':dict(product_type='SPAC')}
        result=engine.analyze(report(stocks),[record(s['name'],'동반') for s in stocks],products,day='2026-11-05',policy=POLICY)
        self.assertEqual(len(result['excluded_stocks']),2)
        self.assertEqual(result['mapping']['coverage']['total'],3)
        self.assertEqual(result['analysis']['sectors'],[])
        self.assertEqual(result['stocks'][2]['eligibility_status'],'pending')

    def test_unknown_cap_is_pending_and_unverified_amount_not_used(self):
        stocks=[stock('000001','2'),stock('000002','3')]
        result=engine.analyze(report(stocks),[record(s['name'],'동반') for s in stocks],{},day='2026-11-05',policy=POLICY)
        sector=result['analysis']['sectors'][0]
        self.assertEqual(sector['confirmed_leaders'],[])
        self.assertTrue(all(s['capital_status']=='unverified' for s in sector['leaders']))
        self.assertEqual(sector['verified_turnover_won'],'0')
        self.assertFalse(result['analysis']['amount_score_applied'])

    def test_cap_threshold_and_verified_amount_reuse(self):
        stocks=[stock('000001','2'),stock('000002','3'),stock('000003','4')]
        r=report(stocks)
        r['candidates']=[dict(stock_code='000001',status='held',metrics=dict(market_cap={'value':'300000000000'},turnover={'value':'100000000'}))]
        result=engine.analyze(r,[record(s['name'],'동반') for s in stocks],{},day='2026-11-05',policy=POLICY)
        self.assertEqual(result['analysis']['sectors'][0]['rising_count'],2)
        self.assertEqual(result['stocks'][0]['capital_status'],'below_minimum')

    def test_no_forced_third_leader_and_strength_clipping(self):
        stocks=[stock('000001','99'),stock('000002','2')]
        result=themes(stocks,tags(('000001','동반'),('000002','동반')))
        self.assertEqual(len(result['sectors'][0]['leaders']),2)
        self.assertEqual(result['sectors'][0]['components']['strength'],'6')

    def test_verified_amount_only_breaks_otherwise_equal_leader_ties(self):
        stocks=[stock('000001','2',None,None,turnover_won='100'),
                stock('000002','2',None,None,turnover_won='200')]
        result=themes(stocks,tags(('000001','동반'),('000002','동반')))
        self.assertEqual(result['sectors'][0]['leaders'][0]['stock_code'],'000002')
        self.assertEqual(result['sectors'][0]['verified_turnover_won'],'300')

    def test_telegram_unicode_payload_round_trip(self):
        text='한글😀\n'*2500
        chunks=engine.telegram_chunks(text)
        self.assertEqual(''.join(chunks),text)
        self.assertTrue(all(len(c.encode('utf-16-le'))//2<=4096 for c in chunks))

    def test_default_aj_engine_unchanged(self):
        result=analyze_themes([stock('000001','1')],tags(('000001','A')))
        self.assertNotIn('rising_analysis',result)
        self.assertEqual(result['themes'][0]['leadership_status'],'unconfirmed')

    def test_invalid_weights_rejected(self):
        with self.assertRaises(ValueError):
            analyze_themes([],[],rising_policy=dict(POLICY,breadth=-1))


if __name__=='__main__': unittest.main()
