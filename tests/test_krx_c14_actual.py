"""Opt-in real saved KRX values; diagnostic anchors are not real D0 selections."""
from decimal import Decimal, localcontext
import json
import os
from pathlib import Path
import unittest

from kiwoom_collector import krx_c14 as ar
from kiwoom_collector.daily_chart import fingerprint
from kiwoom_collector.krx_adr import sha


@unittest.skipUnless(os.environ.get('STOCK1_AR2_ACTUAL')=='1','explicit actual KRX checks only')
class ActualKrxC14Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.output=ar.ROOT/'20261104-v1'
        cls.summary=json.loads((cls.output/'summary.json').read_text(encoding='utf8'))
        codes=[x['stock_code'] for x in cls.summary['items']]
        cls.rows,cls.index=ar.read_inputs(codes)
        cls.cards={c:json.loads((cls.output/c/'card.json').read_text(encoding='utf8')) for c in codes}
        cls.episodes=json.loads((cls.output/'diagnostic-tracking.json').read_text(encoding='utf8'))

    def test_real_ohlcv_input_exact_identity_and_actual_receipt_cutoff(self):
        self.assertEqual(len(self.rows),33);self.assertEqual(len(self.index.open_dates),261)
        for c in self.rows:
            document=json.loads((self.output/c/'input.json').read_text(encoding='utf8'))
            raw={r['day']:r for r in self.rows[c]}
            self.assertEqual(document['provider'],'KRX');self.assertNotIn('ka10081',str(document))
            for bar in document['bars']:
                self.assertEqual(bar['stock_code'],c)
                for k in ('open','high','low','close','volume'):self.assertEqual(bar[k],raw[bar['trade_date']][k])
                self.assertEqual(bar['available_at'],raw[bar['trade_date']]['collected_at'])
                self.assertLessEqual(ar.tracking.timestamp(bar['available_at']),ar.tracking.timestamp(document['as_of']))

    def test_card_schema_fingerprint_provisional_and_independent_averages(self):
        for c,card in self.cards.items():
            self.assertEqual(card['schema_version'],'arvis-chart-card-v1')
            self.assertEqual(card['card_reference'],fingerprint({k:v for k,v in card.items() if k!='card_reference'}))
            observed=json.loads((self.output/c/'observed.json').read_text(encoding='utf8'))
            strict=json.loads((self.output/c/'strict_context.json').read_text(encoding='utf8'))
            self.assertFalse(observed['price_basis_verified'])
            self.assertTrue(all(v['value'] is None for v in strict['moving_averages'].values()))
            byday={r['day']:r for r in self.rows[c]}
            for n in (5,20,60):
                ma=card['moving_averages'][f'MA{n}']
                if ma['value'] is None:continue
                dates=observed['moving_averages'][f'MA{n}']['trade_dates']
                with localcontext() as ctx:
                    ctx.prec=50
                    expected=sum(Decimal(byday[d]['close']) for d in dates)/n
                    self.assertEqual(Decimal(ma['value']),expected)
                self.assertEqual(ma['status'],'provisional')

    def test_pngs_actual_full_partial_and_unavailable(self):
        from PIL import Image
        counts={s:sum(x['status']==s for x in self.summary['items']) for s in ('available','partial','unavailable')}
        self.assertEqual(counts,dict(available=30,partial=2,unavailable=1))
        for item in self.summary['items']:
            p=self.output/item['stock_code']/'mini.png'
            if item['status']=='unavailable':self.assertFalse(p.exists());continue
            with Image.open(p) as im:self.assertEqual(im.size,(960,600));im.verify()
            self.assertEqual(item['candles'],60 if item['status']=='available' else {'0158S0':9,'0035S0':22}[item['stock_code']])

    def test_252_high_independent_and_no_short_history_fabrication(self):
        valid=0
        for c,card in self.cards.items():
            if card['high52']['status']!='provisional':continue
            valid+=1;days=card['high52']['provisional']
            raw={r['day']:r for r in self.rows[c]}
            start,end=days['window_start'],days['window_end']
            values=[r for d,r in raw.items() if start<=d<=end]
            self.assertEqual(len(values),252)
            self.assertEqual(Decimal(card['high52']['value']),max(Decimal(r['high']) for r in values))
            self.assertNotEqual(card['high52']['status'],'verified')
        self.assertEqual(valid,26)
        for code in ('0158S0','0035S0','012340'):self.assertIsNone(self.cards[code]['high52']['value'])

    def test_tracking_weekend_offsets_and_independent_price_volume_metrics(self):
        self.assertEqual(sum(r['state']=='completed' for r in self.episodes),32)
        for episode in self.episodes:
            self.assertTrue(episode['diagnostic']);self.assertTrue(episode['d0_unchanged'])
            self.assertEqual([d['trade_date'] for d in episode['days']],['2026-10-26','2026-10-27'])
            raw={r['day']:r for r in self.rows[episode['stock_code']]}
            d0=raw.get('2026-10-23')
            for d in episode['days']:
                if d['status']!='received':continue
                current=raw[d['trade_date']];m=d['metrics']
                for k in ('open','high','low','close','volume'):self.assertEqual(Decimal(m[k]),Decimal(current[k]))
                expected=(Decimal(current['close'])-Decimal(d0['close']))/Decimal(d0['close'])*100
                self.assertLess(abs(Decimal(m['change_d0_percent'])-expected),Decimal('1e-20'))
                self.assertEqual(m['d0_high_break'],Decimal(current['high'])>Decimal(d0['high']))
                self.assertEqual(m['d0_low_break'],Decimal(current['low'])<Decimal(d0['low']))
                self.assertIsNone(m['turnover_won']);self.assertIsNone(m['vwap'])

    def test_historical_morning_excludes_real_later_backfill(self):
        past=json.loads((self.output/'morning-blocked-chart.json').read_text(encoding='utf8'))
        self.assertEqual(past['input']['bars'],[]);self.assertEqual(past['observed']['bars_used'],0)
        for result in json.loads((self.output/'morning-blocked-tracking.json').read_text(encoding='utf8')):
            self.assertEqual(result['adapted_events'],0)
            self.assertTrue(all(not d['metrics'] for d in result['days']))

    def test_calendar_provenance_and_same_day_close_cutoff(self):
        queries=json.loads((self.output/'calendar-queries.json').read_text(encoding='utf8'))
        self.assertTrue(any(q['external_trader_close']['records'] for q in queries[:-1]))
        self.assertTrue(any(q['arvis_capture']['records'] for q in queries[:-1]))
        self.assertTrue(all(q['actual_selection_count']==0 for q in queries))
        self.assertEqual(queries[-1]['external_trader_close']['records'],[])
        self.assertEqual(queries[-1]['external_trader_close']['theme_records'],[])
        self.assertEqual(queries[-1]['tracking'],[])

    def test_etf_evidence_and_protected_source_databases(self):
        product=json.loads((self.output/'122630-product-review.json').read_text(encoding='utf8'))
        self.assertEqual(product['product_type'],'ETF');self.assertEqual(product['policy_state'],'excluded')
        before=json.loads((ar.ROOT/'protected-before.json').read_text(encoding='utf8'))
        allowed={str(Path(ar.__file__).parent/'analyst'/f) for f in ('context.py','observed_daily.py','card.py')}
        for name,value in before.items():
            if name not in allowed:self.assertEqual(sha(name),value,name)
        self.assertEqual(sha(ar.aq.SOURCE),self.summary['source_sha256'])
        self.assertEqual(json.loads((ar.ROOT/'d0-inventory.json').read_text(encoding='utf8')),[])


if __name__=='__main__':unittest.main()
