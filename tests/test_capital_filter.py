"""Offline capital integration and provisional exploration policy."""
from copy import deepcopy
import unittest
import test_market_cap as cap_tests
from test_cross_adapter import document
from test_bubble_cross import source
from kiwoom_collector import capital_filter as cf, cross_adapter as ca, bubble_cross as ui


class CapitalFilterTests(unittest.TestCase):
    def inputs(self):
        helper=cap_tests.MarketCapTests();helper.setUp()
        cap,proof,cash=helper.verified()
        doc=document();doc['turnover']['rows'][0]['trde_prica']='100000'
        report=ca.analyze_pair(doc,min_turnover_eok=1000)
        row=report['rows'][0]
        cash.update(sector_co_rise=True,sector_co_rise_verified=True)
        witness=dict(cross_context=cf.context_key(report,row),capital=proof,turnover=cash)
        return report,cap,{'005930':witness}

    def run_pair(self,report,cap,proofs):
        return cf.integrate(report,[cap],reference_at='2026-10-27T15:30:00+09:00',proofs=proofs)['rows'][0]

    def test_verified_closing_review_still_not_automatic_candidate(self):
        report,cap,proofs=self.inputs();row=self.run_pair(report,cap,proofs)
        self.assertTrue(row['qualification']['final_review_allowed'])
        self.assertFalse(row['candidate_confirmed'])
        self.assertEqual(row['stock_code'],'005930')
        self.assertEqual(row['capital_band']['band'],'primary_exploration')

    def test_unit_diagnostic_exact_and_not_promoted(self):
        result=cf.unit_diagnostic({'raw':dict(mac='9847037',cur_prc='+1348000',flo_stk='730492')})
        self.assertEqual(result['price_times_thousand_shares_won'],'984703216000000')
        self.assertEqual(result['difference_won'],'484000000')
        self.assertFalse(result['unit_runtime_verified'])

    def test_band_boundaries_and_no_upper_exclusion(self):
        for value,band in [('299999999999','below_floor'),('300000000000','primary_exploration'),
            ('5000000000000','primary_exploration'),('5000000000001','extended_exploration'),
            ('10000000000000','extended_exploration'),('10000000000001','mega_cap_leader'),(None,'unknown')]:
            r=cf.capital_band(value)
            self.assertEqual(r['band'],band)
            self.assertFalse(r['upper_cap_exclusion']);self.assertFalse(r['top5_observation_excluded'])

    def test_mega_cap_not_excluded_from_review_or_sector(self):
        helper=cap_tests.MarketCapTests();helper.setUp();helper.fixture['records'][0]['body']['mac']='100001'
        cap,proof,_=helper.verified();report,_,proofs=self.inputs();proofs['005930']['capital']=proof
        row=self.run_pair(report,cap,proofs)
        self.assertTrue(row['qualification']['final_review_allowed'])
        self.assertTrue(row['qualification']['priority_review'])
        self.assertFalse(row['qualification']['sector_detection_excluded'])
        self.assertEqual(row['capital_band']['band'],'mega_cap_leader')

    def test_missing_other_stock_and_input_immutability(self):
        report,cap,proofs=self.inputs();before=deepcopy(report)
        result=cf.integrate(report,[cap])
        self.assertEqual(report,before)
        self.assertIsNone(result['rows'][1]['capital_reference'])
        self.assertEqual(result['rows'][0]['capital_band']['band'],'unknown')
        self.assertFalse(result['rows'][0]['capital_band_reference']['verified'])

    def test_duplicate_code_mismatch_and_mixed_environment(self):
        report,cap,proofs=self.inputs()
        self.assertIsNone(cf.integrate(report,[cap,cap])['rows'][0]['capital_reference'])
        cap['raw']['stk_cd']='000660'
        self.assertIn('market_cap_code_mismatch',self.run_pair(report,cap,proofs)['qualification']['reasons'])
        cap['environment']='real'
        with self.assertRaises(ValueError):cf.integrate(report,[cap])

    def test_time_units_sector_and_context_must_all_be_verified(self):
        for field,value in [('market_asof','2026-10-26T15:30:00+09:00'),
            ('unit_runtime_verified',False),('sector_co_rise',False),('basis','intraday_provisional')]:
            report,cap,proofs=self.inputs();proofs['005930']['turnover'][field]=value
            self.assertFalse(self.run_pair(report,cap,proofs)['qualification']['final_review_allowed'])
        report,cap,proofs=self.inputs();proofs['005930']['cross_context']='wrong'
        self.assertFalse(self.run_pair(report,cap,proofs)['qualification']['final_review_allowed'])

    def test_cap_and_turnover_floor_boundaries(self):
        for cap_value,cash_value,expected in [('2999.99','100000',False),('3000','99999.99',False),('3000','100000',True)]:
            helper=cap_tests.MarketCapTests();helper.setUp();helper.fixture['records'][0]['body']['mac']=cap_value
            cap,proof,_=helper.verified();report,_,proofs=self.inputs()
            report['rows'][0].update(turnover_raw=cash_value,turnover_won_diagnostic=str(cf.Decimal(cash_value)*1000000))
            proofs['005930']['capital']=proof;proofs['005930']['cross_context']=cf.context_key(report,report['rows'][0])
            self.assertIs(self.run_pair(report,cap,proofs)['qualification']['final_review_allowed'],expected)

    def test_etf_excluded(self):
        helper=cap_tests.MarketCapTests();helper.setUp();helper.fixture['records'][0]['security_type']='ETF'
        cap,proof,_=helper.verified();report,_,proofs=self.inputs();proofs['005930']['capital']=proof
        self.assertEqual(self.run_pair(report,cap,proofs)['qualification']['status'],'대상 제외')

    def test_ui_capital_reference_not_leaked_into_earlier_frame(self):
        _,cap,_=self.inputs();bundle=ui.build(source(),capital_records=[cap])
        old=ui.frame_html(bundle['timeline']['frames'][0],bundle)
        self.assertNotIn('선택 회차 적용 미검증',old)
        current=ui.frame_html(bundle['timeline']['frames'][-1],bundle)
        self.assertIn('선택 회차 적용 미검증',current)
        self.assertIn('시총 3,000억원 이상<b>미확인</b>',current)

    def test_band_edges_through_eligibility_and_bubble_card(self):
        cases=[('2999.99','below_floor',False,'하한 미만'),
            ('3000','primary_exploration',True,'핵심 탐색'),
            ('3000.01','primary_exploration',True,'핵심 탐색'),
            ('49999.99','primary_exploration',True,'핵심 탐색'),
            ('50000','primary_exploration',True,'핵심 탐색'),
            ('50000.01','extended_exploration',True,'확장 탐색'),
            ('99999.99','extended_exploration',True,'확장 탐색'),
            ('100000','extended_exploration',True,'확장 탐색'),
            ('100000.01','mega_cap_leader',True,'초대형 주도주')]
        for mac,band,review,label in cases:
            with self.subTest(mac=mac):
                helper=cap_tests.MarketCapTests();helper.setUp()
                helper.fixture['records'][0]['body']['mac']=mac
                cap,proof,_=helper.verified();report,_,proofs=self.inputs()
                proofs['005930']['capital']=proof
                row=self.run_pair(report,cap,proofs)
                self.assertEqual(row['capital_band']['band'],band)
                self.assertIs(row['qualification']['final_review_allowed'],review)
                self.assertFalse(row['candidate_confirmed'])
                self.assertFalse(row['qualification']['sector_detection_excluded'])
                self.assertTrue(row['qualification']['priority_review'])
                html=ui.card(dict(code='005930',name='fixture',rank=1),row,True)
                self.assertIn(label,html)

    def test_1646_cap_is_reference_only_for_1500_pair(self):
        helper=cap_tests.MarketCapTests();helper.setUp()
        helper.fixture['records'][0].update(request_started_at='2026-10-27T16:46:55+09:00',
            response_received_at='2026-10-27T16:46:56+09:00')
        cap=helper.cap();before=deepcopy(cap);report,_,_=self.inputs()
        result=cf.integrate(report,[cap]);row=result['rows'][0]
        self.assertEqual(cap,before)
        self.assertEqual(result['capital_integration']['reference_at'],'2026-10-27T15:00:00+09:00')
        self.assertEqual(row['capital_reference']['raw']['mac'],cap['raw']['mac'])
        self.assertEqual(row['capital_reference']['won_diagnostic'],cap['market_cap_won_diagnostic'])
        self.assertEqual(row['capital_reference']['retrieved_at'],'2026-10-27T16:46:56+09:00')
        self.assertIsNone(row['market_cap_won_verified'])
        self.assertEqual(row['capital_band']['band'],'unknown')
        self.assertFalse(row['qualification']['final_review_allowed'])
        self.assertIsNone(result['rows'][1]['capital_reference'])

    def test_sk_hynix_cap_not_reused_for_samsung(self):
        helper=cap_tests.MarketCapTests();helper.setUp()
        helper.fixture['records'][0]['requested_code']='000660'
        helper.fixture['records'][0]['body']['stk_cd']='000660'
        cap=helper.cap();report,_,_=self.inputs()
        result=cf.integrate(report,[cap])
        samsung=next(r for r in result['rows'] if r['stock_code']=='005930')
        hynix=next(r for r in result['rows'] if r['stock_code']=='000660')
        self.assertIsNone(samsung['capital_reference'])
        self.assertEqual(hynix['capital_reference']['raw']['stk_cd'],'000660')
        self.assertFalse(samsung['candidate_confirmed']);self.assertFalse(hynix['candidate_confirmed'])
