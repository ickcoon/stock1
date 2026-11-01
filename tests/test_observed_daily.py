"""Synthetic historical daily overlay: strict evidence gates remain intact."""
import unittest
from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch
from test_daily_supply import source,plan,replay
from kiwoom_collector.daily_supply_cli import ReplayTransport
from kiwoom_collector.daily_chart import parse_response
from kiwoom_collector.analyst.observed_daily import analyze_observed_daily
from kiwoom_collector.analyst.card import chart_card
from kiwoom_collector.analyst.context import analyze_context
from test_weekly_hybrid import weekly_fixture
from test_analyst_chart import ASOF


def page_for(count=253,changes=None):
    rows,_=source(count)
    if changes:changes(rows)
    reply=ReplayTransport(replay(plan(),[rows])).fetch(plan(),{},float('inf'))
    return parse_response(reply['body'],request=plan(),response_environment='mock',
        request_started_at=reply['request_started_at'],response_received_at=reply['response_received_at'],
        source='SYNTHETIC',cont_yn='N',response_api_id='ka10081')


class ObservedDailyTests(unittest.TestCase):
    def setUp(self):self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))

    def test_ma_slope_and_volume_proxy_remain_provisional(self):
        result=analyze_observed_daily([page_for()],as_of=ASOF,data_origin='synthetic_fixture')
        self.assertEqual(result['moving_averages']['MA20']['value'],'342.5')
        self.assertEqual(result['moving_averages']['MA20']['slope']['delta'],'1.0')
        self.assertTrue(result['trend']['ma20_above_ma60'])
        self.assertEqual(result['supply_proxy']['status'],'provisional')
        self.assertEqual(len(result['supply_proxy']['areas']),5)
        self.assertFalse(result['price_basis_verified']);self.assertFalse(result['order_signal'])

    def test_short_history_only_missing_windows_unavailable(self):
        r=analyze_observed_daily([page_for(5)],as_of=ASOF,data_origin='synthetic_fixture')
        self.assertEqual(r['moving_averages']['MA5']['status'],'provisional')
        self.assertEqual(r['moving_averages']['MA200']['status'],'unavailable')
        self.assertEqual(r['supply_proxy']['status'],'unavailable')

    def test_bad_recent_bar_not_silently_bridged(self):
        p=page_for(changes=lambda rows:rows[0].update(high_pric='1'))
        r=analyze_observed_daily([p],as_of=ASOF,data_origin='synthetic_fixture')
        self.assertTrue(r['held_dates'])
        self.assertEqual(r['moving_averages']['MA3']['status'],'unavailable')

    def test_duplicate_and_future_receipt_rejected(self):
        p=page_for()
        with self.assertRaises(ValueError):analyze_observed_daily([p,p],as_of=ASOF,data_origin='synthetic_fixture')
        with self.assertRaises(ValueError):analyze_observed_daily([p],as_of='2026-10-27T10:00:00+09:00',data_origin='synthetic_fixture')

    def test_card_fills_unavailable_fields_and_uses_daily_close_for_weekly_distance(self):
        from kiwoom_collector.analyst.hybrid import analyze_hybrid
        doc,page,args=weekly_fixture();doc['price_basis']['verified']=False
        args['weekly_price_basis']=None
        report=analyze_hybrid(doc,[page],**args)
        observed=analyze_observed_daily([page_for()],as_of=ASOF,data_origin='synthetic_fixture')
        card=chart_card(report,observed_daily=observed)
        self.assertEqual(card['moving_averages']['MA20']['status'],'provisional')
        self.assertEqual(card['resistance']['status'],'provisional')
        self.assertEqual(card['high52']['comparison_price']['trade_date'],'2026-10-27')
        self.assertEqual(card['high52']['comparison_price']['kind'],'latest_saved_daily_close_not_live_quote')
        self.assertEqual(card['high52']['status'],'provisional')
        self.assertFalse(card['confirmed_buy_signal'])
        self.assertEqual(report['primary_high52']['status'],'unavailable')

    def test_pure_arithmetic_helpers_match_existing_engine(self):
        from kiwoom_collector.analyst.chart import mean_close,volume_areas
        bars=[dict(close=Decimal(n),high=Decimal(n+1),low=Decimal(n-1),volume=Decimal(n),
            trade_date=f'2026-01-{n:02d}') for n in range(2,23)]
        self.assertEqual(mean_close(bars,3),Decimal(21))
        self.assertIsNone(mean_close(bars,200))
        areas=volume_areas(bars[-20:],Decimal(20))
        self.assertEqual(len(areas),5)
        self.assertEqual(areas[0]['typical_price'],'22')
        self.assertTrue(areas[0]['typical_above_observed_price'])

    def test_card_overlay_never_replaces_verified_and_preserves_primary(self):
        doc,_,_=weekly_fixture();report=analyze_context(doc)
        observed=analyze_observed_daily([page_for()],as_of=ASOF,data_origin='synthetic_fixture')
        before=deepcopy(report);card=chart_card(report,observed_daily=observed)
        self.assertEqual(report,before)
        self.assertEqual(card['moving_averages']['MA20']['status'],'verified')
        bad=deepcopy(observed);bad['stock_code']='000660'
        with self.assertRaises(ValueError):chart_card(report,observed_daily=bad)
