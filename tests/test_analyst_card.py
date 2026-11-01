"""Synthetic card mapping and compatibility; no HTTP or DB."""
import json
import unittest
from copy import deepcopy
from unittest.mock import patch
from kiwoom_collector.analyst.card import chart_card
from kiwoom_collector.analyst.hybrid import analyze_hybrid
from kiwoom_collector.analyst.context import analyze_context
from test_weekly_hybrid import weekly_fixture


class CardTests(unittest.TestCase):
    def setUp(self):self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))

    def test_verified_hybrid_preserves_source_and_daily_metrics(self):
        doc,page,args=weekly_fixture();report=analyze_hybrid(doc,[page],**args);before=deepcopy(report)
        card=chart_card(report,stock_name='SYNTHETIC NAME')
        self.assertEqual(report,before);self.assertEqual(card['high52']['status'],'verified')
        self.assertEqual(card['high52']['age_weeks']['value'],1)
        self.assertEqual(card['moving_averages']['MA20']['value'],'342.5')
        self.assertEqual(card['moving_averages']['MA20']['position'],'above')
        self.assertIsNotNone(card['moving_averages']['MA200']['gap_percent'])
        self.assertEqual(card['resistance']['analysis'],report['daily']['supply_proxy'])
        self.assertFalse(card['order_signal']);json.dumps(card,allow_nan=False)

    def test_standalone_provisional_with_missing_daily_and_name(self):
        doc,page,args=weekly_fixture();args['weekly_price_basis']=None
        report=analyze_hybrid(None,[page],**args,weekly_context=dict(stock_code='005930',environment='mock',
            market=doc['market'],data_origin='synthetic_fixture',as_of=doc['as_of'],reference_date='2026-10-27'))
        card=chart_card(report)
        self.assertEqual(card['high52']['status'],'provisional')
        self.assertEqual(card['high52']['value'],'153')
        self.assertEqual(card['high52']['distance_status'],'provisional')
        self.assertIsNone(card['stock_name'])
        self.assertTrue(all(v['status']=='unavailable' for v in card['moving_averages'].values()))
        self.assertEqual(card['resistance']['status'],'unavailable')
        self.assertFalse(card['confirmed_buy_signal'])

    def test_daily_only_uses_existing_high_and_insufficient_data(self):
        doc,_,_=weekly_fixture();card=chart_card(analyze_context(doc))
        self.assertEqual(card['high52']['status'],'verified')
        self.assertIsNotNone(card['high52']['price_vs_high_percent'])
        doc,_,_=weekly_fixture(daily_count=5);card=chart_card(analyze_context(doc))
        self.assertEqual(card['high52']['status'],'unavailable')
        self.assertEqual(card['moving_averages']['MA200']['status'],'unavailable')

    def test_schema_and_scope_rejected(self):
        with self.assertRaises(ValueError):chart_card({})
        doc,page,args=weekly_fixture();report=analyze_hybrid(doc,[page],**args)
        report['daily']['stock_code']='000660'
        with self.assertRaises(ValueError):chart_card(report)
