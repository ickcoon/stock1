"""Focused position/card integration checks; no network or operational writers."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from test_mini_chart import MiniChartTests
from kiwoom_collector import c16_bridge
from kiwoom_collector.analyst.mini_chart import chart_series


class PositionTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('no network')))
        self.card,self.observed=MiniChartTests().fixture()

    def test_observations_do_not_change_original_card_or_claim_volume_profile(self):
        before=deepcopy(self.card);series=chart_series(self.card,self.observed)
        result=c16_bridge.position(series,self.observed)
        self.assertEqual(before,self.card)
        self.assertFalse(result['volume_at_price_available'])
        self.assertFalse(result['rules']['score_applied'])
        self.assertIsNone(result['current_intraday_price'])

    def test_incomplete_ma_history_is_not_promoted(self):
        series=chart_series(self.card,self.observed);observed=deepcopy(self.observed)
        observed['moving_averages']['MA60']['value']=None
        self.assertEqual(c16_bridge.position(series,observed)['observation_type'],'데이터 부족')

    def test_candidate_future_reference_rejected_before_output(self):
        with self.assertRaisesRegex(ValueError,'candidate_not_known'):
            c16_bridge.build(dict(reference_at='2026-11-06T09:50:00+09:00'),daily_db='unused',as_of='2026-11-05T09:40:00+09:00',output='unused')

    def test_actual_saved_bundle_images_and_evidence(self):
        root=Path(__file__).resolve().parents[1]/'diagnostics/c16-a-20261106-final'
        if not root.exists():self.skipTest('local actual artifact not generated')
        report=json.loads((root/'top5.json').read_text(encoding='utf8'))
        self.assertEqual(len(report['items']),9)
        for item in report['items']:
            card=json.loads((root/item['card_path']).read_text(encoding='utf8'))
            self.assertEqual(card['mini_chart_metadata']['candle_count'],60)
            self.assertLess(card['mini_chart_metadata']['latest_date'],card['as_of'][:10])
            self.assertEqual((root/item['mini_chart_path']).read_bytes()[:8],b'\x89PNG\r\n\x1a\n')
        html=(root/'dashboard.html').read_text(encoding='utf8')
        self.assertEqual(html.count('class="top5-card"'),9)
        self.assertIn('일봉 OHLCV 추정 영역',html)
