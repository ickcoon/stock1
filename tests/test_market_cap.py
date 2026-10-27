"""Offline ka10001 parsing, eligibility boundaries and transport budgets."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock,patch
from kiwoom_collector import market_cap as mc

FIXTURE=Path(__file__).parent/'fixtures/market_cap_synthetic.json'


class MarketCapTests(unittest.TestCase):
    def setUp(self):self.fixture=json.loads(FIXTURE.read_text(encoding='utf8'))

    def cap(self):return mc.parse_fixture(self.fixture)['records'][0]

    def verified(self):
        self.fixture['records'][0].update(request_started_at='2026-10-27T15:30:01+09:00',response_received_at='2026-10-27T15:30:02+09:00')
        cap=self.cap()
        proof=dict(for_record=cap['fingerprint'],environment='mock',source='SYNTHETIC evidence',mac_unit_runtime_verified=True,market_cap_basis_verified=True,
                   market_asof='2026-10-27T15:30:00+09:00',trade_date='2026-10-27')
        cash=dict(stock_code='005930',environment='mock',turnover_won='100000000000',turnover_rank=40,
            market_asof=proof['market_asof'],trade_date='2026-10-27',unit_runtime_verified=True,basis='closing_cumulative',closing_basis_verified=True,market_conditions_verified=True,evidence='SYNTHETIC closing evidence')
        return cap,proof,cash

    def evaluate(self,cap,proof,cash,**kwargs):
        return mc.eligibility(cap,cash,at='2026-10-27T15:30:00+09:00',evidence=proof,**kwargs)

    def test_declared_conversion_preserves_raw_zero_and_signed_price(self):
        cap=self.cap()
        self.assertEqual(cap['requested_code'],'005930')
        self.assertEqual(cap['raw']['cur_prc'],'+030000')
        self.assertEqual(cap['market_cap_won_diagnostic'],'300000000000')
        self.assertFalse(cap['mac_unit_runtime_verified'])
        self.assertIsNone(cap['market_cap_meets_threshold'])

    def test_flo_stk_conflicting_unit_never_assumed_or_multiplied(self):
        cap=self.cap()
        self.assertEqual(cap['listed_stock_unit_status'],'unverified_spec_conflict')
        self.assertIsNone(cap['listed_shares'])
        self.assertIsNone(cap['market_cap_reconstructed_from_price_and_shares'])

    def test_cap_3000_boundary_with_synthetic_external_evidence(self):
        for amount,expected in (('2999.99',False),('3000',True),('3000.01',True)):
            self.fixture['records'][0]['body']['mac']=amount
            cap,proof,cash=self.verified()
            result=self.evaluate(cap,proof,cash)
            self.assertIs(result['checks']['market_cap_3000_eok'],expected)
            self.assertFalse(result['candidate_confirmed'])

    def test_closing_turnover_1000_boundary(self):
        cap,proof,cash=self.verified()
        for amount,expected in (('99999999999',False),('100000000000',True),('100000000001',True)):
            cash['turnover_won']=amount
            self.assertIs(self.evaluate(cap,proof,cash)['checks']['closing_turnover_1000_eok'],expected)

    def test_unverified_unit_basis_or_source_never_passes(self):
        for field in ('mac_unit_runtime_verified','market_cap_basis_verified','source','for_record'):
            cap,proof,cash=self.verified();proof[field]=False
            self.assertIsNone(self.evaluate(cap,proof,cash)['checks']['market_cap_3000_eok'])

    def test_intraday_amount_not_closing_and_does_not_remove_sector(self):
        cap,proof,cash=self.verified();cash['basis']='intraday_provisional';cash['turnover_won']='1'
        r=self.evaluate(cap,proof,cash)
        self.assertIsNone(r['checks']['closing_turnover_1000_eok'])
        self.assertFalse(r['sector_detection_excluded'])

    def test_top5_priority_is_not_mandatory_eligibility(self):
        cap,proof,cash=self.verified()
        r=self.evaluate(cap,proof,cash,ranking_0198=8)
        self.assertEqual(r['status'],'장마감 필수조건 충족')
        self.assertFalse(r['priority_review']);self.assertFalse(r['candidate_confirmed'])
        self.assertTrue(self.evaluate(cap,proof,cash,ranking_0198=1)['priority_review'])

    def test_etf_etn_unknown_type_exclusion_and_no_name_inference(self):
        for kind in ('ETF','ETN','SPAC','preferred_stock','unknown'):
            self.fixture['records'][0]['security_type']=kind
            cap,proof,cash=self.verified()
            self.assertIsNone(self.evaluate(cap,proof,cash)['checks']['market_cap_3000_eok'])
        self.fixture['records'][0]['security_type']='common_stock'
        self.fixture['records'][0].pop('security_type_evidence')
        self.assertEqual(self.cap()['security_type'],'unknown')

    def test_missing_negative_nonfinite_and_wrong_types(self):
        original=deepcopy(self.fixture)
        for field in ('mac','flo_stk','cur_prc'):
            for value in (None,'','NaN','Infinity','bad',3000):
                self.fixture=deepcopy(original);self.fixture['records'][0]['body'][field]=value
                self.assertEqual(self.cap()['status'],'invalid')
        for field in ('mac','flo_stk'):
            self.fixture=deepcopy(original);self.fixture['records'][0]['body'][field]='-1'
            self.assertEqual(self.cap()['status'],'invalid')

    def test_code_mismatch_never_normalized_or_padded(self):
        for value in ('5930','005930_NX','000660'):
            self.fixture['records'][0]['body']['stk_cd']=value
            self.assertIn('response_code_mismatch',self.cap()['issues'])
        with self.assertRaises(ValueError):mc.request_plan([5930])

    def test_exchange_codes_and_mock_restriction(self):
        for code in ('039490_NX','039490_AL'):
            with self.assertRaises(ValueError):mc.request_plan([code],'mock')
            self.assertEqual(mc.request_plan([code],'real')['requests'][0]['stk_cd'],code)

    def test_duplicate_fixture_and_plan_dedup_budget(self):
        self.fixture['records'].append(deepcopy(self.fixture['records'][0]))
        self.assertIn('duplicate_code',self.cap()['issues'])
        p=mc.request_plan(['005930','005930','000660'])
        self.assertEqual(p['max_query_attempts'],2)
        self.assertEqual(p['duplicate_codes'],['005930'])
        with self.assertRaises(ValueError):mc.request_plan([f'{i:06}' for i in range(11)])

    def test_mixed_environment_and_wrong_turnover_code(self):
        cap,proof,cash=self.verified();cash['environment']='real'
        with self.assertRaises(ValueError):self.evaluate(cap,proof,cash)
        cash['environment']='mock';cash['stock_code']='000660'
        self.assertIn('turnover_code_mismatch',self.evaluate(cap,proof,cash)['reasons'])

    def test_time_mismatch_stale_day_and_no_market_time(self):
        for value in ('2026-10-26T15:30:00+09:00','2026-10-27T15:29:59+09:00',None):
            cap,proof,cash=self.verified();cash['market_asof']=value
            self.assertEqual(self.evaluate(cap,proof,cash)['status'],'검증 보류')
        self.fixture['records'][0]['response_received_at']='2026-10-27T14:00:00+09:00'
        self.assertIn('invalid_observation_interval',self.cap()['issues'])

    def test_future_market_time_and_tampered_record_never_qualify(self):
        cap,proof,cash=self.verified()
        cap['response_received_at']='2026-10-27T15:00:00+09:00'
        self.assertIsNone(self.evaluate(cap,proof,cash)['checks']['market_cap_3000_eok'])
        cap,proof,cash=self.verified()
        cap['market_cap_won_diagnostic']='999999999999'
        self.assertIsNone(self.evaluate(cap,proof,cash)['checks']['market_cap_3000_eok'])
        cap,proof,cash=self.verified();cap['raw']['mac']='9999'
        self.assertIsNone(self.evaluate(cap,proof,cash)['checks']['market_cap_3000_eok'])

    def test_http_api_error_invalid_object_and_continuation(self):
        for status in (401,403,429,500):
            self.fixture['records'][0]['http_status']=status
            self.assertEqual(self.cap()['status'],'invalid')
        self.fixture['records'][0]['http_status']=200
        self.fixture['records'][0]['body']['return_code']=8005
        self.assertEqual(self.cap()['return_code'],8005)
        self.fixture['records'][0]['body']=[]
        self.assertEqual(self.cap()['status'],'invalid')

    def test_injected_transport_once_exact_path_no_oauth_or_replay(self):
        r=self.fixture['records'][0]
        transport=Mock(return_value=dict(r,environment='mock'))
        client=mc.IndependentClient(transport,network_approved=True,max_queries=1)
        client.fetch_once('005930')
        self.assertEqual(transport.call_args.kwargs,dict(api_id='ka10001',path='/api/dostk/stkinfo',body={'stk_cd':'005930'},environment='mock'))
        with self.assertRaises(ValueError):client.fetch_once('005930')
        with self.assertRaises(ValueError):client.fetch_once('000660')
        self.assertEqual(transport.call_count,1)

    def test_approval_before_transport_and_failure_stops_batch(self):
        transport=Mock(side_effect=RuntimeError('private error'))
        client=mc.IndependentClient(transport)
        with self.assertRaises(ValueError):client.fetch_once('005930')
        transport.assert_not_called()
        client=mc.IndependentClient(transport,network_approved=True)
        with self.assertRaisesRegex(ValueError,'details omitted'):client.fetch_once('005930')
        with self.assertRaises(ValueError):client.fetch_once('000660')
        self.assertEqual(transport.call_count,1)

    def test_cross_target_selection_and_existing_bubble_compatibility(self):
        from test_bubble_cross import source
        from kiwoom_collector.bubble_cross import build
        report=build(source())['comparisons'][500]
        before=deepcopy(report)
        plan=mc.plan_from_cross(report)
        self.assertEqual(plan['requests'],[{'stk_cd':'005930'}])
        self.assertEqual(report,before)
        self.assertFalse(report['candidate_confirmed'])

    def test_offline_parse_has_no_io_network_or_input_mutation(self):
        before=deepcopy(self.fixture)
        with patch('socket.socket.connect',side_effect=AssertionError('No network')),patch('builtins.open',side_effect=AssertionError('No files')):
            result=mc.parse_fixture(self.fixture)
        self.assertEqual(self.fixture,before)
        self.assertEqual(result['actual_api_calls'],0)
        self.assertFalse(result['candidate_confirmed'])


if __name__=='__main__':unittest.main()
