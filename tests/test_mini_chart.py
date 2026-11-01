"""Mini-chart PNG/JSON contracts using existing synthetic C14 fixtures."""
import json
from pathlib import Path
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch
from test_observed_daily import page_for
from test_weekly_hybrid import weekly_fixture
from test_analyst_chart import ASOF
from kiwoom_collector.analyst.observed_daily import analyze_observed_daily
from kiwoom_collector.analyst.context import analyze_context
from kiwoom_collector.analyst.card import chart_card
from kiwoom_collector.analyst.mini_chart import chart_series,with_mini_chart
from kiwoom_collector.analyst.chart import mean_close
from decimal import Decimal


class MiniChartTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)

    def fixture(self,count=253):
        observed=analyze_observed_daily([page_for(count)],as_of=ASOF,data_origin='synthetic_fixture')
        doc,_,_=weekly_fixture(daily_count=count);doc['price_basis']['verified']=False
        card=chart_card(analyze_context(doc),observed_daily=observed)
        return card,observed

    def test_png_60_candles_and_warmup_ma_alignment(self):
        card,observed=self.fixture();before=deepcopy(card)
        result=with_mini_chart(card,observed,self.root/'mini.png')
        self.assertEqual(result['mini_chart_status'],'available')
        self.assertEqual((self.root/'mini.png').read_bytes()[:8],b'\x89PNG\r\n\x1a\n')
        from PIL import Image
        with Image.open(self.root/'mini.png') as im:self.assertEqual(im.size,(960,600))
        series=result['mini_chart_metadata'];self.assertEqual(series['candle_count'],60)
        self.assertEqual(series['input_bars'],119)
        for n in (5,20,60):
            ma=series['moving_averages'][f'MA{n}']
            self.assertTrue(all(v is not None for v in ma))
            self.assertEqual(Decimal(ma[-1]),Decimal(observed['moving_averages'][f'MA{n}']['value']))
            warmup=[dict(close=Decimal(b['close'])) for b in observed['chart_bars'][:60]]
            if n==60:self.assertEqual(Decimal(ma[0]),mean_close(warmup,60))
        self.assertEqual(series['latest_close'],observed['reference_close']['value'])
        self.assertEqual(card,before)
        for key,value in card.items():
            if key!='card_reference':self.assertEqual(result[key],value)
        self.assertEqual(result['mini_chart_path'],'mini.png');json.dumps(result,allow_nan=False)

    def test_resistance_line_and_recent_high_fallback(self):
        card,observed=self.fixture();close=Decimal(observed['reference_close']['value'])
        card['resistance']['analysis']['areas']=[dict(typical_price=str(close+10)),dict(typical_price=str(close+5))]
        series=chart_series(card,observed)
        self.assertEqual(Decimal(series['horizontal_line']['value']),close+5)
        self.assertEqual(series['horizontal_line']['kind'],'potential_resistance')
        card['resistance']['analysis']['areas']=[]
        series=chart_series(card,observed)
        self.assertEqual(Decimal(series['horizontal_line']['value']),max(Decimal(b['high']) for b in series['bars']))

    def test_resistance_band_exact_decimal_bounds_and_json(self):
        card,observed=self.fixture()
        card['resistance']['analysis']['areas']=[dict(typical_price='1000')]
        card['resistance']['status']='provisional'
        result=with_mini_chart(card,observed,self.root/'zone.png')
        self.assertEqual(result['resistance_zone_low'],'992.5000')
        self.assertEqual(result['resistance_zone_high'],'1007.5000')
        self.assertEqual(result['resistance_zone_status'],'provisional')
        self.assertEqual(result['resistance_zone_source'],'c14_20bar_volume_proxy_typical_price')
        self.assertEqual(result['resistance'],card['resistance'])
        self.assertEqual(result['moving_averages'],card['moving_averages'])

    def test_transparent_region_labels_candles_ma_and_volume_preserved(self):
        from matplotlib.figure import Figure
        original=Figure.savefig;seen={}
        def capture(fig,*args,**kwargs):
            price,volume=fig.axes
            seen['band']=any(p.get_alpha()==0.22 for p in price.patches)
            seen['labels']=[t.get_text() for t in price.texts]
            seen['ma']={line.get_label() for line in price.lines}
            seen['volume_bars']=len(volume.patches)
            return original(fig,*args,**kwargs)
        card,observed=self.fixture()
        card['resistance']['analysis']['areas']=[dict(typical_price='1000')]
        with patch.object(Figure,'savefig',new=capture):result=with_mini_chart(card,observed,self.root/'visual.png')
        self.assertEqual(result['mini_chart_status'],'available')
        self.assertTrue(seen['band'])
        self.assertTrue(any(t.startswith('Upper ') for t in seen['labels']))
        self.assertTrue(any(t.startswith('Lower ') for t in seen['labels']))
        self.assertTrue({'MA5','MA20','MA60'}<=seen['ma'])
        self.assertEqual(seen['volume_bars'],60)

    def test_no_resistance_does_not_relabel_recent_high_as_zone(self):
        card,observed=self.fixture(5)
        card['resistance']['analysis']['areas']=[]
        result=with_mini_chart(card,observed,self.root/'no-zone.png')
        self.assertEqual(result['mini_chart_status'],'partial')
        self.assertEqual(result['resistance_zone_status'],'unavailable')
        self.assertIsNone(result['resistance_zone_low'])
        self.assertEqual(result['mini_chart_metadata']['horizontal_line']['kind'],'recent_60_bar_high')

    def test_structural_pair_exact_prices_and_threshold_rejection(self):
        from kiwoom_collector.analyst.mini_chart import structural_resistance
        def bar(day,o,h,l,c):return dict(trade_date=day,open=Decimal(o),high=Decimal(h),low=Decimal(l),close=Decimal(c))
        bull=bar('2026-10-28',231000,247000,228000,245000)
        bear=bar('2026-10-29',241000,249000,226000,230500)
        zone=structural_resistance([bull,bear],Decimal(220500))
        self.assertEqual(zone['low'],'245000');self.assertEqual(zone['high'],'249000')
        self.assertEqual(zone['status'],'provisional');self.assertFalse(zone['score_applied'])
        self.assertIsNone(structural_resistance([bull,bear],Decimal(250000)))
        self.assertIsNone(structural_resistance([bull,dict(bear,high=Decimal(242000))],Decimal(220500)))
        self.assertIsNone(structural_resistance([bull,dict(bear,trade_date='2026-11-20')],Decimal(220500)))

    def test_latest_above_price_structure_prioritized(self):
        from kiwoom_collector.analyst.mini_chart import structural_resistance
        def bar(day,o,h,l,c):return dict(trade_date=day,open=Decimal(o),high=Decimal(h),low=Decimal(l),close=Decimal(c))
        pair=[bar('2026-10-20',231000,247000,228000,245000),bar('2026-10-21',241000,249000,226000,230500),
              bar('2026-10-28',231000,247000,228000,245000),bar('2026-10-29',241000,249000,226000,230500)]
        self.assertEqual(structural_resistance(pair,Decimal(220500))['bull']['trade_date'],'2026-10-28')

    def test_user_ma_colors_match_lines_and_legend(self):
        from matplotlib.figure import Figure
        from matplotlib.colors import to_hex
        from kiwoom_collector.analyst.mini_chart import MA_COLORS
        self.assertEqual(MA_COLORS,{'MA5':'#d62728','MA20':'#c46a00','MA60':'#1f6b3a'})
        original=Figure.savefig;seen={}
        def capture(fig,*args,**kwargs):
            price=fig.axes[0]
            seen['lines']={l.get_label():to_hex(l.get_color()) for l in price.lines if l.get_label() in MA_COLORS}
            legend=price.get_legend()
            seen['legend']={t.get_text():to_hex(h.get_color()) for t,h in zip(legend.get_texts(),legend.legend_handles) if t.get_text() in MA_COLORS}
            return original(fig,*args,**kwargs)
        card,observed=self.fixture()
        with patch.object(Figure,'savefig',new=capture):result=with_mini_chart(card,observed,self.root/'colors.png')
        self.assertEqual(result['mini_chart_status'],'available')
        self.assertEqual(seen['lines'],MA_COLORS);self.assertEqual(seen['legend'],MA_COLORS)

    def test_short_history_partial_missing_ma_and_bad_data(self):
        card,observed=self.fixture(5)
        result=with_mini_chart(card,observed,self.root/'short.png')
        self.assertEqual(result['mini_chart_status'],'partial')
        self.assertEqual(result['mini_chart_metadata']['candle_count'],5)
        self.assertTrue(all(v is None for v in result['mini_chart_metadata']['moving_averages']['MA60']))
        for kind in ('duplicate','missing','held','scope'):
            bad=deepcopy(observed)
            if kind=='duplicate':bad['chart_bars'].append(bad['chart_bars'][-1])
            elif kind=='missing':bad['chart_bars'][-1]['close']=None
            elif kind=='held':bad['held_dates']=[bad['chart_bars'][-1]['trade_date']]
            else:bad['stock_code']='000660'
            self.assertEqual(with_mini_chart(card,bad,self.root/(kind+'.png'))['mini_chart_status'],'unavailable')

    def test_render_failure_does_not_break_card_generation(self):
        card,observed=self.fixture()
        with patch('kiwoom_collector.analyst.mini_chart.render_png',side_effect=OSError('synthetic disk failure')):
            result=with_mini_chart(card,observed,self.root/'no.png')
        self.assertEqual(result['mini_chart_status'],'unavailable');self.assertIsNone(result['mini_chart_path'])
        doc,_,_=weekly_fixture();doc['price_basis']['verified']=False
        result=chart_card(analyze_context(doc),observed_daily=observed,mini_chart_output=self.root/'absent'/'mini.png')
        self.assertEqual(result['mini_chart_status'],'unavailable')
        self.assertEqual(result['moving_averages'],card['moving_averages'])
