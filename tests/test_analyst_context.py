"""C14-B uses only explicit synthetic, point-in-time completed bars."""
from copy import deepcopy
from decimal import Decimal
import json
import unittest
from unittest.mock import patch

from kiwoom_collector.analyst import analyze_stock, analyze_stock_context
from test_analyst_chart import fixture


class ChartContextTests(unittest.TestCase):
    def run_context(self, doc, **kwargs):
        before = deepcopy(doc)
        with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            result = analyze_stock_context(doc, **kwargs)
        self.assertEqual(doc, before)
        json.dumps(result, allow_nan=False)
        self.assertFalse(result['order_signal'])
        self.assertFalse(result['candidate_eligibility_changed'])
        return result

    def test_reuses_legacy_analysis_and_all_five_close_comparisons(self):
        doc = fixture(253, price=9999)
        r = self.run_context(doc)
        self.assertEqual(r['analysis'], analyze_stock(doc))
        self.assertEqual(r['reference_close']['value'], '352')
        self.assertEqual(r['close_comparisons'], {f'MA{n}':'above' for n in (3,5,20,60,200)})
        self.assertEqual(Decimal(r['close_short_disparity']['MA3']['gap_percent']).quantize(
            Decimal('0.000000000001')), Decimal('0.284900284900'))
        self.assertTrue(r['supporting_factors']['ma20_above_ma60'])
        self.assertTrue(r['supporting_factors']['close_above_ma200'])
        self.assertEqual(r['explanation']['state'], 'trend_good')

    def test_prior_252_excludes_reference_and_ignores_older_extreme(self):
        doc = fixture(260)
        doc['bars'][0]['high'] = '999999'
        doc['bars'][-1].update(open='400', high='401', low='399', close='400')
        r = self.run_context(doc)
        high = r['high52']
        self.assertEqual(high['prior_high'], '360')
        self.assertEqual(high['window_start'], doc['bars'][-253]['trade_date'])
        self.assertEqual(high['window_end'], doc['bars'][-2]['trade_date'])
        self.assertEqual(high['high_dates'], [doc['bars'][-2]['trade_date']])
        self.assertTrue(high['close_breakout_confirmed'])
        self.assertLess(Decimal(high['distance_below_high_percent']), 0)
        self.assertTrue(r['supporting_factors']['new_252_session_high'])

    def test_equal_high_is_retest_not_breakout_and_all_high_dates_retained(self):
        doc = fixture(253)
        doc['bars'][-3]['high'] = '353'
        doc['bars'][-1].update(open='353', high='354', low='352', close='353')
        high = self.run_context(doc)['high52']
        self.assertEqual(high['high_dates'], [b['trade_date'] for b in doc['bars'][-3:-1]])
        self.assertFalse(high['close_breakout_confirmed'])
        self.assertTrue(high['high_retest'])
        self.assertEqual(Decimal(high['distance_below_high_percent']), 0)

    def test_insufficient_history_never_pads(self):
        for count in (0,2,20,200,252):
            with self.subTest(count=count):
                doc = fixture(max(count,1))
                if not count:
                    doc['bars'] = [];doc['calendar']['completed_sessions'] = []
                r = self.run_context(doc)
                self.assertEqual(r['high52']['status'], 'unavailable')
                self.assertIsNone(r['high52']['prior_high'])
                self.assertIsNone(r['high52']['close_breakout_confirmed'])
                self.assertEqual(len(r['analysis']['evidence']['bars']), count)

    def test_missing_prior_bar_is_not_replaced_by_an_older_bar(self):
        doc = fixture(260)
        missing = doc['bars'].pop(-240)['trade_date']
        r = self.run_context(doc)
        self.assertEqual(r['high52']['reason'], 'missing_or_invalid_prior_bar')
        self.assertEqual(r['high52']['missing_trade_dates'], [missing])
        self.assertIsNone(r['high52']['prior_high'])

    def test_invalid_and_duplicate_prior_bars_fail_closed(self):
        for mode in ('future','duplicate','bad_high'):
            doc = fixture(253)
            row = doc['bars'][5]
            if mode == 'future':row.update(available_at='2099-01-01T00:00:00+09:00',high='777777')
            if mode == 'duplicate':doc['bars'].append(deepcopy(row))
            if mode == 'bad_high':row['high']='1'
            with self.subTest(mode=mode):
                r = self.run_context(doc)
                self.assertIsNone(r['high52']['prior_high'])
                self.assertNotIn('777777', json.dumps(r))

    def test_missing_reference_does_not_use_yesterdays_close(self):
        doc = fixture(254);doc['bars'].pop()
        r = self.run_context(doc)
        self.assertIsNone(r['reference_close'])
        self.assertEqual(r['high52']['reason'], 'reference_close_unavailable')
        self.assertTrue(all(v is None for v in r['close_comparisons'].values()))
        self.assertEqual(r['explanation']['state'], 'data_review_required')

    def test_price_basis_and_calendar_review_are_required(self):
        for field, key in (('price_basis','comparable'),('price_basis','corporate_actions_checked'),('calendar','verified')):
            doc = fixture(253);doc[field][key] = False
            r = self.run_context(doc)
            self.assertIsNone(r['reference_close'])
            self.assertIsNone(r['high52']['prior_high'])
            self.assertEqual(r['explanation']['state'], 'data_review_required')

    def test_close_zone_precedence_and_equal_ma_boundary(self):
        for close, expected in (('349','conservative_observation'),('300','avoidance_candidate')):
            doc = fixture(253)
            doc['bars'][-1].update(open=close,high=close,low=close,close=close)
            r = self.run_context(doc)
            self.assertEqual(r['explanation']['state'], expected)
        doc = fixture(253)
        for row in doc['bars']:
            row.update(open='100',high='100',low='100',close='100')
        r = self.run_context(doc)
        self.assertEqual(r['close_comparisons']['MA5'], 'equal')
        self.assertFalse(r['supporting_factors']['ma20_above_ma60'])
        self.assertFalse(r['supporting_factors']['close_above_ma200'])
        self.assertEqual(r['explanation']['state'], 'active_observation')

    def test_ohlcv_supply_is_an_estimate_with_nearest_overhead_distance(self):
        doc = fixture(253)
        doc['bars'][-10].update(open='370',high='390',low='360',close='375',volume='999999')
        r = self.run_context(doc)
        self.assertEqual(r['explanation']['state'], 'resistance_caution')
        self.assertTrue(r['supply_proxy']['overhead_resistance_possible'])
        area = r['supply_proxy']['nearest_overhead_area']
        self.assertEqual(area['trade_date'],doc['bars'][-10]['trade_date'])
        self.assertGreater(Decimal(area['distance_from_close_percent']),0)
        self.assertEqual(r['high_vs_supply_distance'], 'supply_nearer')
        self.assertEqual(area['relative_to_prior_high'], 'overlapping')
        self.assertIn('추정치',r['supply_proxy']['notice'])
        self.assertFalse(r['scoring']['applied'])

    def test_zero_or_missing_volume_is_unknown_not_no_resistance(self):
        for volume in ('0',None):
            doc = fixture(253);doc['bars'][-10]['volume'] = volume
            r = self.run_context(doc)
            self.assertEqual(r['supply_proxy']['status'],'unavailable')
            self.assertIsNone(r['supply_proxy']['overhead_resistance_possible'])
            self.assertEqual(r['supply_proxy']['areas'],[])
            self.assertEqual(r['high52']['status'],'available')

    def test_high_and_supply_distance_order_use_the_same_close_denominator(self):
        doc = fixture(253)
        doc['bars'][-10].update(open='396',high='400',low='395',close='396',volume='999999')
        r = self.run_context(doc)
        self.assertEqual(r['high52']['prior_high'],'400')
        self.assertEqual(Decimal(r['supply_proxy']['nearest_overhead_area']['typical_price']),397)
        self.assertEqual(r['high_vs_supply_distance'],'supply_nearer')
        self.assertGreater(Decimal(r['high52']['rise_to_high_percent']),
                           Decimal(r['supply_proxy']['nearest_overhead_area']['distance_from_close_percent']))

    def test_candidate_envelope_preserves_c12_decision_without_promoting_it(self):
        doc = fixture(253)
        context = dict(stock_code=doc['stock_code'],environment='mock',market='KRX',
            data_origin='synthetic_fixture',available_at=doc['as_of'],source='SYNTHETIC C12',
            decision=dict(state='pending',reasons=['type_unverified']))
        before = deepcopy(context)
        r = self.run_context(doc,candidate_context=context)
        self.assertEqual(context,before)
        self.assertEqual(r['candidate_context'],before)
        self.assertIn('후보 승격하지 않음',r['explanation']['reasons'][-1])
        for change in (dict(stock_code='000001'),dict(environment='real'),dict(market='NXT'),
                       dict(data_origin='api_response'),dict(available_at='2099-01-01T00:00:00+09:00')):
            with self.subTest(change=change),self.assertRaisesRegex(ValueError,'candidate_context'):
                analyze_stock_context(doc,candidate_context=dict(context,**change))

    def test_future_price_does_not_replace_completed_close_or_leak(self):
        doc=fixture(253)
        doc['price'].update(value='888888',available_at='2099-01-01T00:00:00+09:00')
        r=self.run_context(doc)
        self.assertEqual(r['reference_close']['value'],'352')
        self.assertNotIn('888888',json.dumps(r))

    def test_order_independence_and_reference_hash(self):
        doc=fixture(253);baseline=self.run_context(doc)
        doc['bars'].reverse();doc['calendar']['completed_sessions'].reverse()
        r=self.run_context(doc)
        self.assertEqual(r['high52'],baseline['high52'])
        self.assertEqual(r['close_comparisons'],baseline['close_comparisons'])
        self.assertEqual(len(r['context_reference']),64)


if __name__ == '__main__':unittest.main()
