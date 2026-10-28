"""Offline official-shape fixtures; finality proofs are explicitly SYNTHETIC."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import date
from decimal import Decimal, localcontext
import json
from pathlib import Path
import unittest

from kiwoom_collector import daily_chart as daily, daily_tracking_adapter as adapter
from kiwoom_collector import leader_tracking as tracking
from test_leader_tracking import snapshot, calendar

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/daily_chart_synthetic.json').read_text(encoding='utf-8'))


def request(api='ka10081', **kwargs):
    return daily.DailyRequest(api, kwargs.get('stock_code', '005930'), kwargs.get('environment', 'mock'),
                              kwargs.get('base_dt', '20261028'), kwargs.get('adjustment', '0'))


def parse(rows=None, req=None, **kwargs):
    req = req or request()
    body = dict(stk_cd=req.stock_code, return_code=0, return_msg='SYNTHETIC',
                **{daily.LIST_KEYS[req.api_id]: deepcopy(FIXTURE['rows'] if rows is None else rows)})
    values = dict(request=req, response_environment=req.environment,
                  request_started_at='2026-10-28T16:00:00+09:00',
                  response_received_at='2026-10-28T16:00:01+09:00', source='SYNTHETIC saved response',
                  cont_yn='N', response_api_id=req.api_id)
    values.update(kwargs)
    body = values.pop('body', body)
    return daily.parse_response(body, **values)


def proofs(page, snap=None):
    snap = snap or snapshot()
    basis = 'regular_session' if page.request.api_id == 'ka11081' else 'chart_session'
    return tuple(adapter.ClosingEvidence(page.fingerprint, row.fingerprint, snap.episode_id,
                 row.trade_date.isoformat()+'T15:30:00+09:00', '2026-10-28T16:01:00+09:00',
                 'SYNTHETIC reviewed finality, units, scope and D0 compatibility', basis,
                 'KRW', 'share', 'million_KRW', True, True, 'final') for row in page.rows)


def convert(page=None, evidence=None, snap=None, **kwargs):
    page = page or parse(); snap = snap or snapshot()
    values = dict(calendar=calendar(), as_of='2026-10-28T17:00:00+09:00',
                  expected_session_basis='regular_session' if page.request.api_id == 'ka11081' else 'chart_session',
                  evidence=proofs(page, snap) if evidence is None else evidence)
    values.update(kwargs)
    return adapter.to_tracking((page,), snap, **values)


class DailyChartTests(unittest.TestCase):
    def test_api_specific_array_keys_and_request(self):
        for api in daily.LIST_KEYS:
            page = parse(req=request(api))
            self.assertEqual(len(page.rows), 3)
            self.assertEqual(page.request.body, FIXTURE['request'])
            self.assertEqual(page.units['turnover'], 'million_KRW')
            self.assertEqual(page.units['status'], 'documented_not_runtime_verified')
        with self.assertRaises(ValueError):
            parse(req=request('ka11081'), body=dict(stk_cd='005930', return_code=0, stk_dt_pole_chart_qry=[]))

    def test_exact_string_codes_and_markets(self):
        self.assertEqual(request().stock_code, '005930')
        self.assertEqual(request(stock_code='0000A0').stock_code, '0000A0')
        for code, market in [('005930_NX', 'NXT'), ('005930_AL', 'SOR')]:
            self.assertEqual(request(stock_code=code, environment='real').market, market)
            with self.assertRaises(ValueError): request(stock_code=code)
        for code in (5930, '5930', 'A005930', '005930 ', '005930_X'):
            with self.subTest(code=code), self.assertRaises(ValueError): request(stock_code=code)

    def test_price_signs_and_decimal_cash_precision(self):
        page = parse(); row = page.rows[1]
        self.assertEqual((row.open, row.high, row.low, row.close), (99, 101, 96, 98))
        self.assertEqual(row.raw['cur_prc'], '-98')
        self.assertEqual(row.turnover_won, Decimal('59000'))
        raw = deepcopy(FIXTURE['rows'][:1]); raw[0]['trde_prica'] = '1234567890123.123456'
        with localcontext() as context:
            context.prec = 6
            parsed = parse(rows=raw).rows[0]
        self.assertEqual(parsed.turnover_won, Decimal('1234567890123123456'))

    def test_raw_fields_and_immutable_input_detachment(self):
        rows = deepcopy(FIXTURE['rows']); page = parse(rows=rows)
        rows[0]['cur_prc'] = '1'; copy = page.raw; copy['stk_cd'] = '000660'
        self.assertEqual(page.raw['stk_cd'], '005930')
        self.assertEqual(page.rows[0].close, 106)
        self.assertEqual(page.rows[2].raw['extra_raw_field'], 'retained')
        with self.assertRaises(FrozenInstanceError): page.rows[0].close = 1

    def test_missing_volume_turnover_and_prices_not_estimated(self):
        raw = deepcopy(FIXTURE['rows'][:1])
        for field in ('trde_qty', 'trde_prica', 'open_pric', 'cur_prc'): raw[0][field] = ''
        row = parse(rows=raw).rows[0]
        self.assertTrue(all(getattr(row, field) is None for field in ('volume', 'turnover_raw', 'turnover_won', 'open', 'close')))
        self.assertFalse(convert(parse(rows=raw)).observations)

    def test_missing_turnover_is_unknown_not_derived_or_vwap(self):
        raw = deepcopy(FIXTURE['rows'][:1]); raw[0].pop('trde_prica')
        result = convert(parse(rows=raw)); self.assertFalse(result.holds)
        self.assertIsNone(result.observations[0].turnover_won)
        self.assertIsNone(result.observations[0].price_volume_sum)
        self.assertIsNone(result.observations[0].vwap_volume)

    def test_invalid_numeric_negative_volume_and_saturation(self):
        for field, value in [('trde_qty', '-1'), ('trde_qty', '1.5'), ('trde_qty', '4294967295'),
                             ('trde_prica', '-1'), ('cur_prc', 'NaN'), ('cur_prc', True),
                             ('cur_prc', '--100'), ('cur_prc', 'Infinity')]:
            raw = deepcopy(FIXTURE['rows'][:1]); raw[0][field] = value
            with self.subTest(field=field, value=value):
                page = parse(rows=raw); self.assertTrue(page.rows[0].issues)
                self.assertFalse(convert(page).observations)

    def test_inconsistent_ohlc_blocked_after_absolute_prices(self):
        raw = deepcopy(FIXTURE['rows'][:1]); raw[0]['high_pric'] = '-95'
        page = parse(rows=raw)
        self.assertIn('inconsistent_ohlc', page.rows[0].issues)
        self.assertFalse(convert(page).observations)

    def test_dates_are_valid_and_future_rows_quarantined(self):
        for value in ('20260230', '2026-10-28', 20261028, '20261029'):
            raw = deepcopy(FIXTURE['rows'][:1]); raw[0]['dt'] = value
            with self.subTest(value=value):
                page = parse(rows=raw); self.assertTrue(page.rows[0].issues)
                if page.rows[0].trade_date: self.assertFalse(convert(page).observations)
        raw = deepcopy(FIXTURE['rows'][:1]); raw[0]['dt'] = '20261028'
        page = parse(rows=raw, req=request(base_dt='20261026'))
        self.assertIn('future_or_after_base_date', page.rows[0].issues)

    def test_descending_order_preserved_and_bad_order_quarantined(self):
        page = parse(); self.assertEqual(page.ordering, 'descending')
        self.assertEqual(page.rows[0].trade_date, date(2026, 10, 28))
        for rows in (list(reversed(FIXTURE['rows'])), [FIXTURE['rows'][0], FIXTURE['rows'][2], FIXTURE['rows'][1]]):
            result = convert(parse(rows=rows))
            self.assertFalse(result.observations)
            self.assertTrue(all('daily_order_unverified' in h[2] for h in result.holds))

    def test_duplicate_dates_rejected_without_picking_winner(self):
        page = parse(rows=[FIXTURE['rows'][0], FIXTURE['rows'][0]])
        self.assertIn('duplicate_daily_dates', page.issues)
        self.assertFalse(convert(page).observations)

    def test_continuation_metadata_preserved_never_followed(self):
        page = parse(cont_yn='Y', next_key='SYNTHETIC_CURSOR')
        self.assertEqual(page.pagination, 'more_available')
        self.assertEqual(page.next_key, 'SYNTHETIC_CURSOR')
        self.assertTrue(convert(page).observations)
        self.assertEqual(parse(cont_yn=None).pagination, 'unknown')
        self.assertFalse(convert(parse(cont_yn='Y', next_key='')).observations)

    def test_api_response_identity_and_failures(self):
        for kwargs in ({'response_environment': 'real'}, {'response_api_id': 'ka11081'}, {'http_status': 429},
                       {'response_received_at': '2026-10-28T15:59:00+09:00'},
                       {'response_received_at': '2026-10-28T16:00:01'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): parse(**kwargs)
        for code in (False, None, 1, '1'):
            with self.assertRaises(ValueError): parse(body=dict(stk_cd='005930', return_code=code, stk_dt_pole_chart_qry=[]))
        for code in ('000660', 5930, None):
            with self.assertRaises(ValueError): parse(body=dict(stk_cd=code, return_code=0, stk_dt_pole_chart_qry=[]))

    def test_row_code_mismatch_and_snapshot_mismatch(self):
        raw = deepcopy(FIXTURE['rows'][:1]); raw[0]['stk_cd'] = '000660'
        self.assertFalse(convert(parse(rows=raw)).observations)
        for key, value in [('stock_code', '000660'), ('environment', 'real'), ('market', 'NXT')]:
            snap = replace(snapshot(), **{key: value}, d0_bar=None)
            with self.subTest(key=key), self.assertRaises(ValueError): convert(snap=snap)

    def test_adjusted_history_never_used_as_d0_observed_price(self):
        page = parse(req=request(adjustment='1'))
        result = convert(page)
        self.assertFalse(result.observations)
        self.assertTrue(all('adjusted_history_not_d0_observed_prices' in h[2] for h in result.holds))
        self.assertEqual(page.request.adjustment, '1')
        raw = deepcopy(FIXTURE['rows'][:1]); raw[0].update(upd_stkpc_tp='8,16', upd_rt='-50.00')
        self.assertEqual(parse(rows=raw).rows[0].raw['upd_stkpc_tp'], '8,16')

    def test_no_automatic_finality_even_for_past_dates(self):
        result = convert(evidence=())
        self.assertFalse(result.observations)
        self.assertTrue(all('closing_evidence_missing' in h[2] for h in result.holds))

    def test_today_incomplete_and_before_close_blocked(self):
        page = parse(rows=FIXTURE['rows'][:1], request_started_at='2026-10-28T10:00:00+09:00',
                     response_received_at='2026-10-28T10:00:01+09:00')
        proof = proofs(page)[0]
        self.assertFalse(convert(page, evidence=(proof,)).observations)
        page = parse(rows=FIXTURE['rows'][:1]); proof = replace(proofs(page)[0], status='incomplete')
        self.assertFalse(convert(page, evidence=(proof,)).observations)

    def test_future_receipt_and_future_verification_blocked(self):
        page = parse(); result = convert(page, as_of='2026-10-28T16:00:00+09:00')
        self.assertFalse(result.observations)
        self.assertTrue(all('response_not_yet_available' in h[2] for h in result.holds))
        evidence = tuple(replace(p, verified_at='2026-10-29T16:00:00+09:00') for p in proofs(page))
        self.assertFalse(convert(page, evidence=evidence).observations)

    def test_units_scope_and_d0_basis_require_bound_evidence(self):
        page = parse(rows=FIXTURE['rows'][:1]); proof = proofs(page)[0]
        for changes in ({'price_unit': None}, {'volume_unit': 'thousand_shares'}, {'turnover_unit': 'KRW'},
                        {'session_basis': 'regular_session'}, {'original_price_basis_verified': False},
                        {'d0_basis_compatible': False}, {'episode_id': 'wrong'}, {'page_fingerprint': 'wrong'},
                        {'row_fingerprint': 'wrong'}, {'source': ''}):
            with self.subTest(changes=changes):
                self.assertFalse(convert(page, evidence=(replace(proof, **changes),)).observations)

    def test_calendar_holiday_and_unknown_day_held(self):
        raw = deepcopy(FIXTURE['rows'][:1]); raw[0]['dt'] = '20261027'
        self.assertFalse(convert(parse(rows=raw)).observations)
        self.assertFalse(convert(calendar=tracking.FixtureCalendar((), (), 'unknown')).observations)

    def test_explicit_halt_distinct_from_zero_or_missing_data(self):
        raw = deepcopy(FIXTURE['rows'][:1])
        raw[0].update(open_pric='0', high_pric='0', low_pric='0', cur_prc='0', trde_qty='0', trde_prica='')
        page = parse(rows=raw); self.assertFalse(convert(page).observations)
        proof = replace(proofs(page)[0], status='halted')
        result = convert(page, evidence=(proof,))
        self.assertEqual(result.observations[0].status, 'halted')
        self.assertIsNone(result.observations[0].close)
        self.assertEqual(convert(parse(rows=[])).observations, ())

    def test_d1_d2_tracking_and_d0_snapshot_unchanged(self):
        for api in daily.LIST_KEYS:
            snap = snapshot(); before = snap.payload_json; page = parse(req=request(api))
            result = convert(page, snap=snap)
            self.assertFalse(result.holds)
            report = tracking.analyze(snap, result.observations, calendar=calendar(), as_of='2026-10-28T17:00:00+09:00')
            self.assertEqual(report['state'], 'completed')
            self.assertIn('correction_with_lower_volume', report['days'][0]['patterns'])
            self.assertEqual(report['days'][1]['metrics']['change_d0_percent'], 6)
            self.assertIsNone(report['days'][1]['metrics']['vwap'])
            self.assertEqual(snap.payload_json, before); self.assertFalse(snap.payload['candidate_confirmed'])

    def test_historical_retrieval_never_backdates_availability(self):
        result = convert()
        snap = snapshot()
        report = tracking.analyze(snap, result.observations, calendar=calendar(), as_of='2026-10-26T17:00:00+09:00')
        self.assertEqual(report['days'][0]['status'], 'not_received')
        self.assertTrue(all(o.available_at.date() == date(2026, 10, 28) for o in result.observations))

    def test_multi_page_overlaps_and_context_mixing_blocked(self):
        first = parse(rows=FIXTURE['rows'][:2], cont_yn='Y', next_key='SYNTHETIC_CURSOR')
        second = parse(rows=FIXTURE['rows'][1:])
        result = adapter.to_tracking((first, second), snapshot(), calendar=calendar(),
                 as_of='2026-10-28T17:00:00+09:00', expected_session_basis='chart_session', evidence=proofs(first)+proofs(second))
        dates = {o.trade_date for o in result.observations}
        self.assertNotIn(date(2026, 10, 26), dates)
        self.assertTrue(any('duplicate_date_across_input' in h[2] for h in result.holds))
        with self.assertRaises(ValueError):
            adapter.to_tracking((first, parse(req=request('ka11081'))), snapshot(), calendar=calendar(),
                 as_of='2026-10-28T17:00:00+09:00', expected_session_basis='chart_session')

    def test_duplicate_proofs_and_wrong_verification_chronology(self):
        page = parse(rows=FIXTURE['rows'][:1]); proof = proofs(page)[0]
        with self.assertRaises(ValueError): convert(page, evidence=(proof, proof))
        self.assertFalse(convert(page, evidence=(replace(proof, verified_at='2026-10-28T15:59:00+09:00'),)).observations)

    def test_copied_price_cannot_reuse_original_fingerprints(self):
        page = parse(); evidence = proofs(page)
        copied = replace(page, rows=(replace(page.rows[0], close=Decimal('107')),)+page.rows[1:])
        self.assertEqual(copied.fingerprint, page.fingerprint)
        self.assertEqual(copied.rows[0].fingerprint, page.rows[0].fingerprint)
        with self.assertRaisesRegex(ValueError, 'does not match its raw response'):
            convert(copied, evidence=evidence)
        self.assertEqual(page.rows[0].close, 106)

    def test_copied_date_cannot_disagree_with_raw_date(self):
        page = parse()
        copied = replace(page, rows=(replace(page.rows[0], trade_date=date(2026, 10, 26)),)+page.rows[1:])
        with self.assertRaisesRegex(ValueError, 'does not match its raw response'):
            convert(copied, evidence=proofs(page))

    def test_copied_volume_and_turnover_cannot_change_units_or_values(self):
        page = parse()
        for changes in ({'volume': Decimal('1200000')}, {'turnover_won': Decimal('0.125')},
                        {'turnover_raw': Decimal('125000')}):
            copied = replace(page, rows=(replace(page.rows[0], **changes),)+page.rows[1:])
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'does not match its raw response'):
                convert(copied, evidence=proofs(page))

    def test_cleared_validation_issues_cannot_bypass_parser_checks(self):
        raw = deepcopy(FIXTURE['rows'][:1]); raw[0]['high_pric'] = '95'
        invalid = parse(rows=raw)
        copied = replace(invalid, rows=(replace(invalid.rows[0], issues=()),))
        with self.assertRaisesRegex(ValueError, 'does not match its raw response'):
            convert(copied, evidence=proofs(invalid))
        unordered = parse(rows=list(reversed(FIXTURE['rows'])))
        with self.assertRaisesRegex(ValueError, 'does not match its raw response'):
            convert(replace(unordered, issues=(), ordering='descending'), evidence=proofs(unordered))

    def test_unchanged_model_copy_still_converts(self):
        page = parse(); copied = replace(page, rows=tuple(replace(row) for row in page.rows))
        original = convert(page)
        self.assertEqual(convert(copied, evidence=proofs(page)), original)
