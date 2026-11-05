"""AR-2 synthetic tests. These are not observations of market outcomes."""
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from kiwoom_collector import krx_c14 as ar, leader_tracking as t, leader_calendar as cal
from kiwoom_collector.daily_chart import fingerprint


NOW='2026-11-04T12:00:00+09:00'


def inputs(n=261):
    days=[];d=date(2025,10,1)
    while len(days)<n:
        if d.weekday()<5:days.append(d.isoformat())
        d+=timedelta(days=1)
    rows=[dict(stock_code='005930',official_code_raw='005930',day=d,market='KOSPI',
        open=str(99+i),high=str(102+i),low=str(98+i),close=str(100+i),volume=str(1000+i),
        issues_json='[]',snapshot_id='fixture-'+d,collected_at='2026-11-03T12:00:00+09:00') for i,d in enumerate(days)]
    return rows,ar.SessionIndex(days,source='SYNTHETIC_fixture',known_at='2026-11-03T12:00:00+09:00')


def anchor():
    return t.Snapshot('005930','real','KRX',date(2026,10,23),t.timestamp('2026-10-23T09:50:00+09:00'),
        t.timestamp(NOW),t.canonical(dict(record_kind='diagnostic_anchor_not_selection')))


class KrxC14Tests(unittest.TestCase):
    def test_uses_c14_card_ma_and_252_high_without_verified_claim(self):
        rows,index=inputs()
        result=ar.make_card(rows,index,code='005930',name='Fixture',as_of=NOW)
        card=result['card']
        self.assertEqual(card['schema_version'],'arvis-chart-card-v1')
        self.assertEqual(card['provenance']['provider'],'KRX')
        self.assertFalse(result['observed']['price_basis_verified'])
        self.assertEqual(card['high52']['status'],'provisional')
        self.assertEqual(card['high52']['value'],max(rows[-253:-1],key=lambda r:Decimal(r['high']))['high'])
        for n in (5,20,60):
            self.assertEqual(Decimal(card['moving_averages'][f'MA{n}']['value']),sum(Decimal(r['close']) for r in rows[-n:])/n)
            self.assertEqual(card['moving_averages'][f'MA{n}']['status'],'provisional')
        self.assertEqual(card['card_reference'],fingerprint({k:v for k,v in card.items() if k!='card_reference'}))
        self.assertNotIn('ka10081',str(result['input']))

    def test_short_history_computes_only_possible_windows(self):
        rows,index=inputs(9)
        result=ar.make_card(rows,index,code='005930',name='Fixture',as_of=NOW)
        self.assertEqual(result['card']['moving_averages']['MA5']['status'],'provisional')
        self.assertIsNone(result['card']['moving_averages']['MA20']['value'])
        self.assertIsNone(result['card']['high52']['value'])

    def test_morning_cutoff_excludes_later_received_history(self):
        rows,index=inputs()
        r=ar.make_card(rows,index,code='005930',name='Fixture',as_of='2026-10-23T09:50:00+09:00')
        self.assertEqual(r['input']['bars'],[])
        self.assertEqual(r['observed']['bars_used'],0)
        self.assertTrue(all(x['value'] is None for x in r['card']['moving_averages'].values()))
        self.assertIsNone(r['card']['high52']['value'])

    def test_same_day_or_unknown_availability_excluded(self):
        rows,index=inputs(3)
        row=deepcopy(rows[-1]);row['day']='2026-11-04';row['collected_at']='2026-11-04T09:00:00+09:00'
        unknown=deepcopy(rows[0]);unknown['collected_at']=None
        visible,held=ar.visible_rows([row,unknown],code='005930',as_of=NOW)
        self.assertEqual(visible,[]);self.assertEqual(len(held),2)

    def test_gap_and_zero_volume_do_not_bridge_ma_window(self):
        rows,index=inputs(70)
        rows[-10]['volume']='0'
        result=ar.make_card(rows,index,code='005930',name='Fixture',as_of=NOW)
        self.assertEqual(result['observed']['bars_used'],9)
        self.assertIsNone(result['card']['moving_averages']['MA20']['value'])
        del rows[-3]
        result=ar.make_card(rows,index,code='005930',name='Fixture',as_of=NOW)
        self.assertEqual(result['observed']['bars_used'],2)

    def test_code_mismatch_rejected(self):
        rows,index=inputs(2)
        with self.assertRaises(ValueError):ar.make_card(rows,index,code='000660',name='Fixture',as_of=NOW)

    def test_weekend_offsets_and_unknown_weekday_stop(self):
        idx=ar.SessionIndex(['2026-10-23','2026-10-26','2026-10-27'],source='fixture',known_at='2026-10-20T00:00:00+09:00')
        self.assertEqual(idx.offset(date(2026,10,23),1),date(2026,10,26))
        self.assertEqual(idx.offset(date(2026,10,23),2),date(2026,10,27))
        gap=ar.SessionIndex(['2026-09-23','2026-09-28'],source='fixture_unknown_empty_weekdays',known_at='2026-10-20T00:00:00+09:00')
        self.assertIsNone(gap.offset(date(2026,9,23),1))

    def test_d0_immutable_diagnostic_tracks_actual_session_offsets(self):
        rows,index=inputs(283)
        # Keep fixture source history before as-of and D0..D2 in the sample.
        rows=[r for r in rows if r['day']<'2026-11-04']
        index=ar.SessionIndex([r['day'] for r in rows],source='fixture',known_at='2026-11-03T12:00:00+09:00')
        s=anchor();before=t.canonical(s.payload)
        result=ar.track(s,rows,index,as_of=NOW,materialized_at=NOW,diagnostic=True)
        self.assertEqual([x['trade_date'] for x in result['days']],['2026-10-26','2026-10-27'])
        self.assertEqual(result['state'],'completed');self.assertEqual(before,t.canonical(s.payload))
        self.assertTrue(result['diagnostic']);self.assertFalse(result['order_signal'])

    def test_no_production_basis_bypass_and_no_future_metrics(self):
        rows,index=inputs(261);s=anchor()
        held=ar.track(s,rows,index,as_of=NOW,materialized_at=NOW)
        self.assertEqual(held['validation_status'],'held')
        self.assertEqual(held['adapted_events'],0)
        past=ar.track(s,rows,index,as_of='2026-10-26T09:50:00+09:00',materialized_at=NOW,diagnostic=True)
        self.assertTrue(all(not d['metrics'] for d in past['days']))
        prod=t.Snapshot('005930','real','KRX',s.d0,s.captured_at,s.created_at,'{}')
        with self.assertRaises(ValueError):ar.track(prod,rows,index,as_of=NOW,materialized_at=NOW,diagnostic=True)

    def test_explicit_production_basis_review_is_bound_and_time_gated(self):
        rows,index=inputs(283)
        s=anchor()
        production=t.Snapshot(s.stock_code,s.environment,s.market,s.d0,s.captured_at,s.created_at,'{"record_kind":"fixture_selection"}')
        proof=dict(episode_id=production.episode_id,basis_id=ar.BASIS,verified=True,
            verified_at='2026-11-04T11:00:00+09:00',evidence_reference='FIXTURE_REVIEW_ONLY',
            price_adjustment_compatible=True,volume_basis_compatible=True)
        before=t.canonical(production.payload)
        r=ar.track(production,rows,index,as_of=NOW,materialized_at=NOW,basis_review=proof)
        self.assertEqual(r['validation_status'],'reviewed');self.assertEqual(r['state'],'completed')
        self.assertEqual(t.canonical(production.payload),before)
        proof['verified_at']='2026-11-05T11:00:00+09:00'
        held=ar.track(production,rows,index,as_of=NOW,materialized_at=NOW,basis_review=proof)
        self.assertEqual(held['validation_status'],'held');self.assertEqual(held['adapted_events'],0)

    def test_new_writer_rejects_operational_target_before_read(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'operations.db';p.write_bytes(b'preserve')
            with self.assertRaises(ValueError):ar.export_charts(['005930'],{},p,as_of=NOW)
            self.assertEqual(p.read_bytes(),b'preserve')

    def test_etf_identified_and_excluded_separately_from_unmatched(self):
        r=cal.supplemental_product('122630')
        self.assertEqual(r['product_type'],'ETF');self.assertEqual(r['policy_state'],'excluded')
        self.assertEqual(r['identity_status'],'official_supplemental_product_match')
        self.assertTrue(r['attention_observation_retained'])
        self.assertEqual(cal.supplemental_product('999999')['state'],'unresolved')


if __name__=='__main__':unittest.main()
