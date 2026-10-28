"""Synthetic, offline point-in-time tracking contracts."""
from dataclasses import FrozenInstanceError, replace
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import unittest

from kiwoom_collector import leader_tracking as t

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/leader_tracking_synthetic.json').read_text(encoding='utf-8'))


def calendar():
    return t.FixtureCalendar(tuple(map(date.fromisoformat, FIXTURE['open_dates'])),
                             tuple(map(date.fromisoformat, FIXTURE['closed_dates'])), FIXTURE['source'])


def bar(day='2026-10-23', case='d0', **changes):
    values = dict(stock_code='005930', environment='mock', market='KRX', trade_date=date.fromisoformat(day),
                  market_at=day+'T15:30:00+09:00', available_at=day+'T15:31:00+09:00',
                  phase='post_market', source='SYNTHETIC OHLCV', **FIXTURE[case])
    values.update(changes)
    return t.Observation(**values)


def snapshot(**changes):
    values = dict(stock_code='005930', environment='mock', market='KRX', d0=date(2026, 10, 23),
                  captured_at='2026-10-23T16:00:00+09:00', created_at='2026-10-23T16:01:00+09:00',
                  payload_json='{"candidate_confirmed":false}', d0_bar=bar())
    values.update(changes)
    return t.Snapshot(**values)


def analyze(events=(), snap=None, cal=None, at='2026-10-28T17:00:00+09:00', **kwargs):
    return t.analyze(snap or snapshot(), events, calendar=cal or calendar(), as_of=at, **kwargs)


class LeaderTrackingTests(unittest.TestCase):
    def test_friday_monday_and_fixture_holiday(self):
        self.assertEqual(calendar().offset(date(2026, 10, 23), 1), date(2026, 10, 26))
        self.assertEqual(calendar().offset(date(2026, 10, 23), 2), date(2026, 10, 28))

    def test_calendar_gaps_never_guessed(self):
        cal = t.FixtureCalendar((date(2026, 10, 23), date(2026, 10, 26)), (), 'incomplete')
        self.assertTrue(all(d['status'] == 'calendar_unknown' for d in analyze(cal=cal)['days']))

    def test_repository_duplicate_and_distinct_episodes(self):
        repo = t.MemoryRepository(); first = snapshot(); repo.add_snapshot(first)
        with self.assertRaises(ValueError): repo.add_snapshot(replace(first, payload_json='{}'))
        second = snapshot(d0=date(2026, 10, 26), captured_at='2026-10-26T16:00:00+09:00',
                          created_at='2026-10-26T16:01:00+09:00', d0_bar=bar('2026-10-26'))
        repo.add_snapshot(second)
        self.assertNotEqual(first.episode_id, second.episode_id)
        event = bar('2026-10-26'); repo.add_event(first.episode_id, event)
        with self.assertRaises(ValueError): repo.add_event(first.episode_id, event)
        self.assertEqual(repo.get(first.episode_id), (first, (event,)))

    def test_snapshot_and_raw_events_immutable(self):
        snap = snapshot(); snap.payload['candidate_confirmed'] = True
        self.assertFalse(snap.payload['candidate_confirmed'])
        with self.assertRaises(FrozenInstanceError): snap.d0 = date(2026, 10, 26)
        with self.assertRaises(FrozenInstanceError): snap.d0_bar.close = 999
        analyze([bar('2026-10-26')], snap=snap)
        self.assertEqual(snap, snapshot())

    def test_future_prices_and_late_arrivals_invisible(self):
        today = bar('2026-10-26', 'correction')
        future = bar('2026-10-28', 'both_breaks')
        late = replace(future, trade_date=today.trade_date, market_at=today.market_at)
        cutoff = '2026-10-26T17:00:00+09:00'
        self.assertEqual(analyze([today], at=cutoff), analyze([today, future, late], at=cutoff))
        self.assertEqual(analyze([today], at=cutoff)['days'][1]['status'], 'pending')

    def test_identity_environment_market_guards(self):
        for key, value in [('stock_code', '000660'), ('environment', 'real'), ('market', 'NXT')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                analyze([bar('2026-10-26', **{key: value})])

    def test_missing_ohlcv_and_vwap_stay_unknown(self):
        event = bar('2026-10-26', open=None, high=None, low=None, close=None, volume=None)
        result = analyze([event])['days'][0]
        self.assertTrue(all(result['metrics'][k] is None for k in ('open', 'high', 'low', 'close', 'volume', 'vwap')))
        self.assertEqual(result['patterns'], ('insufficient_data',))
        self.assertIsNone(analyze([bar('2026-10-26')])['days'][0]['metrics']['vwap'])

    def test_correction_volume_and_threshold_config(self):
        event = bar('2026-10-26', 'correction')
        result = analyze([event])['days'][0]
        self.assertIn('correction_with_lower_volume', result['patterns'])
        self.assertEqual(result['metrics']['volume_d0_percent'], -40)
        self.assertNotIn('correction_with_lower_volume', analyze([event], config=t.PatternConfig(volume_drop_percent=50))['days'][0]['patterns'])

    def test_volume_drop_alone_not_correction(self):
        self.assertNotIn('correction_with_lower_volume', analyze([bar('2026-10-26', volume=100)])['days'][0]['patterns'])

    def test_breakout_breakdown_simultaneous(self):
        patterns = analyze([bar('2026-10-26', 'both_breaks')])['days'][0]['patterns']
        self.assertIn('d0_high_breakout', patterns)
        self.assertIn('d0_low_breakdown', patterns)
        self.assertIn('uptrend_continuation', patterns)

    def test_missing_history_ma_unknown(self):
        result = analyze([bar('2026-10-26')])['days'][0]
        self.assertIsNone(result['metrics']['ma3']); self.assertIsNone(result['metrics']['ma5'])
        self.assertIn('insufficient_data', result['patterns'])

    def test_ma_uses_consecutive_known_sessions_and_near_pattern(self):
        history = [bar(day) for day in ('2026-10-20', '2026-10-21', '2026-10-22')]
        event = bar('2026-10-26', 'correction')
        result = analyze(history + [event])['days'][0]
        self.assertEqual(result['metrics']['ma3'], Decimal(298)/3)
        self.assertEqual(result['metrics']['ma5'], Decimal('99.6'))
        self.assertIn('correction_near_short_ma', result['patterns'])
        late_history = [replace(h, available_at='2026-10-28T16:00:00+09:00') for h in history]
        self.assertIsNone(analyze(late_history + [event])['days'][0]['metrics']['ma3'])

    def test_intraday_not_final_ma_or_completion(self):
        event = bar('2026-10-26', phase='intraday', market_at='2026-10-26T10:00:00+09:00', available_at='2026-10-26T10:01:00+09:00')
        result = analyze([event, bar('2026-10-28')])
        self.assertEqual(result['state'], 'tracking'); self.assertIsNone(result['days'][0]['metrics']['ma3'])

    def test_halt_no_receipt_and_completion_distinct(self):
        self.assertEqual(analyze()['days'][0]['status'], 'not_received')
        for status in ('halted', 'not_received'):
            result = analyze([bar('2026-10-26', status=status), bar('2026-10-28')])
            self.assertEqual(result['days'][0]['status'], status)
            self.assertEqual(result['state'], 'tracking')
        self.assertEqual(analyze([bar('2026-10-26'), bar('2026-10-28')])['state'], 'completed')

    def test_vwap_requires_explicit_numerator_denominator(self):
        event = bar('2026-10-26', price_volume_sum=12300, vwap_volume=100)
        self.assertEqual(analyze([event])['days'][0]['metrics']['vwap'], 123)
        self.assertIsNone(analyze([replace(event, vwap_volume=0)])['days'][0]['metrics']['vwap'])

    def test_d0_late_close_separate_from_selection(self):
        snap = snapshot(d0_bar=None)
        self.assertIsNone(analyze([bar('2026-10-26')], snap=snap)['days'][0]['metrics']['change_d0_percent'])
        self.assertEqual(analyze([bar(), bar('2026-10-26')], snap=snap)['days'][0]['metrics']['change_d0_percent'], 0)
        self.assertIsNone(snap.d0_bar)
        with self.assertRaises(ValueError): snapshot(captured_at='2026-10-23T10:00:00+09:00')

    def test_ambiguous_revision_rejected_and_latest_selected(self):
        event = bar('2026-10-26'); conflict = replace(event, volume=100)
        with self.assertRaises(ValueError): analyze([event, conflict])
        revised = replace(conflict, available_at='2026-10-26T16:00:00+09:00')
        self.assertEqual(analyze([event, revised])['days'][0]['metrics']['volume'], 100)

    def test_validation_timezone_ohlc_and_config(self):
        for kwargs in ({'close': -1}, {'close': 200}, {'volume': 'NaN'}, {'available_at': '2026-10-23T15:00:00'}, {'available_at': '2026-10-23T14:00:00+09:00'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): bar(**kwargs)
        with self.assertRaises(ValueError): t.PatternConfig(volume_drop_percent=101)

    def test_step6d_adapter_preserves_review_and_detaches_inputs(self):
        from test_eligibility_dashboard import build
        report = build()['eligibility']
        snap = t.snapshot_from_review(report, stock_code='005930', captured_at='2026-10-27T15:30:00+09:00',
                                      created_at='2026-10-27T15:31:00+09:00', market='KRX')
        row = next(r for r in report['rows'] if r['stock_code'] == '005930')
        self.assertEqual(snap.payload['original_row'], row)
        self.assertFalse(snap.payload['candidate_confirmed'])
        self.assertIsNone(snap.d0_bar); self.assertIsNone(snap.payload['stock_name'])
        row['qualification']['reasons'].append('later')
        self.assertNotIn('later', snap.payload['qualification']['reasons'])

    def test_step6d_future_and_mixed_rejected(self):
        from test_eligibility_dashboard import build
        for mode in ('future', 'mixed', 'code'):
            report = build()['eligibility']
            if mode == 'future': report['sources']['turnover']['response_received_at'] = '2026-10-27T16:00:00+09:00'
            if mode == 'mixed': report['sources']['turnover']['environment'] = 'real'
            if mode == 'code': report['rows'][0]['capital_reference']['raw']['stk_cd'] = '000660'
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                t.snapshot_from_review(report, stock_code='005930', captured_at='2026-10-27T15:30:00+09:00',
                                       created_at='2026-10-27T15:31:00+09:00', market='KRX')

    def test_derived_provenance_retains_baseline_and_current_sources(self):
        current = bar('2026-10-26')
        inputs = analyze([current])['days'][0]['inputs']
        self.assertEqual({v['fingerprint'] for v in inputs}, {bar().fingerprint, current.fingerprint})
        self.assertTrue(all(v['environment'] == 'mock' and v['version'] == t.VERSION for v in inputs))

    def test_pre_market_partial_data_not_completed(self):
        event = bar('2026-10-26', phase='pre_market', open=None, high=None, low=None,
                    close=None, volume=None, market_at='2026-10-26T08:00:00+09:00',
                    available_at='2026-10-26T08:01:00+09:00')
        result = analyze([event, bar('2026-10-28')])
        self.assertEqual(result['state'], 'tracking')
        self.assertEqual(result['days'][0]['phase'], 'pre_market')
        self.assertIsNone(result['days'][0]['metrics']['ma3'])

    def test_step6d_nested_future_source_rejected(self):
        from test_eligibility_dashboard import build
        report = build()['eligibility']
        report['rows'][0]['source_times']['ranking']['market_asof'] = '2026-10-28T10:00:00+09:00'
        with self.assertRaises(ValueError):
            t.snapshot_from_review(report, stock_code='005930', captured_at='2026-10-27T15:30:00+09:00',
                                   created_at='2026-10-27T15:31:00+09:00', market='KRX')
