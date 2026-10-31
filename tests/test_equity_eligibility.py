"""Synthetic policy and read-only integration checks; no live requests."""
from copy import deepcopy
import hashlib
import unittest
from unittest.mock import patch

from kiwoom_collector import equity_eligibility as e
import test_stock_catalog as fixtures

AT = '2026-10-30T16:02:00+09:00'


def record(code='000001', **changes):
    row = dict(stock_code=code, stock_name='SYNTHETIC ETF name is not evidence',
        security_type='common_stock', type_verified=True, type_evidence='synthetic reviewed proof',
        market='KOSPI', catalog_present=True, data_origin='synthetic_fixture',
        identity_verified=True, listing_status='listed', stock_state='active',
        sector_state='confirmed', representative_sector=e.SECTORS[0], duplicate_primary=False)
    return dict(row, **changes)


class EligibilityTests(unittest.TestCase):
    def test_verified_equities_and_preferred_setting(self):
        for market in ('KOSPI', 'KOSDAQ'):
            self.assertEqual(e.decide(record(market=market))['state'], 'included')
        preferred = record(security_type='preferred_stock')
        self.assertEqual(e.decide(preferred)['state'], 'excluded')
        self.assertEqual(e.decide(preferred, include_preferred=True)['state'], 'included')
        with self.assertRaises(ValueError):
            e.decide(preferred, include_preferred='yes')

    def test_verified_excluded_products_independent_of_market(self):
        for kind in e.EXCLUDED_TYPES:
            for market in ('KOSPI', 'UNKNOWN'):
                with self.subTest(kind=kind, market=market):
                    decision = e.decide(record(security_type=kind, market=market))
                    self.assertEqual(decision, dict(state='excluded', reasons=['excluded_type:' + kind]))

    def test_unverified_types_always_pending_even_excluded_hints(self):
        for kind in e.EXCLUDED_TYPES | {'common_stock', 'preferred_stock', 'unknown'}:
            self.assertEqual(e.decide(record(security_type=kind, type_verified=False))['state'], 'pending')
        for changes in (dict(type_evidence=''), dict(security_type='unknown'), dict(security_type='other'),
                        dict(market='UNKNOWN'), dict(catalog_present=False), dict(data_origin='unverified'),
                        dict(identity_verified=False), dict(listing_status='delisted'),
                        dict(excluded_from_counts=True), dict(stock_state='conflict')):
            self.assertEqual(e.decide(record(**changes))['state'], 'pending')

    def test_overlay_preserves_original_and_rejects_duplicates(self):
        source = dict(records=[record(), record('000002', type_verified=False)])
        before = deepcopy(source)
        result = e.apply_policy(source)
        self.assertEqual(source, before)
        self.assertEqual(result['eligibility_policy']['counts'], dict(included=1, excluded=0, pending=1))
        with self.assertRaisesRegex(ValueError, 'duplicate_classification_code'):
            e.apply_policy(dict(records=[record(), record()]))

    def test_sector_totals_block_products_pending_and_proposals(self):
        rows = [record(), record('000002', security_type='ETF'),
                record('000003', type_verified=False), record('000004', sector_state='proposed'),
                record('000005', duplicate_primary=True), record('000006', representative_sector=e.SECTORS[1])]
        # Forged eligibility cannot bypass evaluation.
        rows[1]['eligibility'] = dict(state='included', reasons=[])
        observations = [dict(stock_code=f'{i:06}', turnover_won=i*100) for i in range(1, 8)]
        report = e.sector_turnover(observations, dict(records=rows))
        self.assertEqual(report['unique_turnover_won'], '700')
        self.assertEqual(report['sector_totals'], {e.SECTORS[0]: '100', e.SECTORS[1]: '600'})
        self.assertEqual(report['excluded']['000002']['reasons'], ['excluded_type:ETF'])
        self.assertEqual(report['excluded']['000007']['reasons'], ['classification_missing'])
        for i in (3, 4, 5):
            self.assertEqual(report['excluded'][f'{i:06}']['state'], 'pending')
        with self.assertRaisesRegex(ValueError, 'duplicate_or_invalid_turnover'):
            e.sector_turnover(observations + observations[:1], dict(records=rows))

    def test_readonly_preview_uses_existing_reviews_and_history(self):
        fixture = fixtures.StockCatalogTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.publish()
        before = hashlib.sha256(fixture.path.read_bytes()).hexdigest()
        with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            report = e.apply_policy(e.preview(fixture.path, at=AT, synthetic=True))
        self.assertEqual(hashlib.sha256(fixture.path.read_bytes()).hexdigest(), before)
        self.assertTrue(report['provenance']['hash_unchanged'])
        self.assertEqual(report['eligibility_policy']['counts'], dict(included=4, excluded=1, pending=0))
        self.assertEqual(report['eligibility_policy']['reason_counts']['excluded'], {'excluded_type:SPAC': 1})
