"""Synthetic practical scope tests. No live services or operational writes."""
from copy import deepcopy
import hashlib
import unittest
from unittest.mock import patch

from kiwoom_collector import equity_eligibility as e
from test_equity_eligibility import record, AT
import test_stock_catalog as fixtures


def sample(code='000001', **changes):
    row = record(code, stock_name='SYNTHETIC', market_code='0', type_verified=False,
                 security_type='unknown')
    row['product_source'] = dict(source='ka10099', run_id='synthetic-run',
        updated_at='2026-10-30T16:00:00+09:00', applied_at='2026-10-30T16:01:00+09:00',
        raw=dict(code=code, name='SYNTHETIC', marketCode='0', marketName='거래소',
                 companyClassName='', orderWarning='0'))
    row.update(changes)
    return row


class CandidateTests(unittest.TestCase):
    def decision(self, row, **kwargs):
        return e.candidate_decision(row, at=AT, **kwargs)

    def test_product_pairs_provisional_and_documented_warning_verified(self):
        for pair, kind in e.PRODUCT_PAIRS.items():
            row = sample(market='UNKNOWN', market_code=pair[0])
            row['product_source']['raw'].update(marketCode=pair[0], marketName=pair[1])
            self.assertEqual(self.decision(row)['state'], 'provisional_excluded')
            self.assertEqual(self.decision(row)['confidence'], 'provisional')
            if kind == 'ETF':
                row['product_source']['raw']['orderWarning'] = '1'
                self.assertEqual(self.decision(row)['state'], 'confirmed_excluded')

    def test_verified_exclusion_and_conflict_precedence(self):
        row = sample(type_verified=True, security_type='SPAC')
        self.assertEqual(self.decision(row)['state'], 'confirmed_excluded')
        row.update(security_type='common_stock', market='UNKNOWN', market_code='8')
        row['product_source']['raw'].update(marketCode='8', marketName='ETF')
        self.assertEqual(self.decision(row)['reasons'], ['product_evidence_conflict'])
        row['type_verified'] = False
        self.assertEqual(self.decision(row)['reasons'], ['product_evidence_conflict'])

    def test_candidate_not_common_stock_verification_and_strict_unchanged(self):
        row = sample()
        before = deepcopy(row)
        report = e.apply_candidates(dict(at=AT, records=[row]))
        self.assertEqual(row, before)
        result = report['records'][0]
        self.assertEqual(result['candidate_eligibility']['state'], 'analysis_candidate')
        self.assertEqual(result['candidate_eligibility']['confidence'], 'provisional')
        self.assertEqual(result['eligibility']['state'], 'pending')
        self.assertFalse(result['type_verified'])
        self.assertEqual(result['security_type'], 'unknown')

    def test_spac_preferred_names_are_pending_only(self):
        for company, name in (('스팩', 'SYNTHETIC'), ('', 'SYNTHETIC우'),
                              ('', 'SYNTHETIC2우B'), ('', 'SYNTHETIC스팩'), ('', 'SYNTHETIC ETF')):
            row = sample(stock_name=name)
            row['product_source']['raw'].update(companyClassName=company, name=name)
            self.assertEqual(self.decision(row)['state'], 'pending')
        row = sample(security_type='preferred_stock', type_verified=True)
        self.assertEqual(self.decision(row)['state'], 'confirmed_excluded')
        self.assertEqual(self.decision(row, include_preferred=True)['state'], 'analysis_candidate')
        row['type_verified'] = False
        self.assertEqual(self.decision(row, include_preferred=True)['state'], 'pending')
        for name in ('메리츠금융지주', '블리츠웨이엔터테인먼트'):
            row = sample(stock_name=name)
            row['product_source']['raw']['name'] = name
            self.assertEqual(self.decision(row)['state'], 'analysis_candidate')

    def test_missing_conflicting_or_future_source_is_held(self):
        for change in ('missing', 'future', 'mismatch', 'market', 'warning', 'origin', 'absent'):
            row = sample()
            if change == 'missing': row['product_source'] = None
            if change == 'future': row['product_source']['applied_at'] = '2026-10-31T16:01:00+09:00'
            if change == 'mismatch': row['product_source']['raw']['code'] = '999999'
            if change == 'market': row['product_source']['raw']['marketName'] = 'ETF'
            if change == 'warning': row['product_source']['raw']['orderWarning'] = '1'
            if change == 'origin': row['data_origin'] = 'unverified'
            if change == 'absent': row['catalog_present'] = False
            with self.subTest(change=change):
                self.assertEqual(self.decision(row)['state'], 'pending')

    def test_readonly_integration_and_existing_sector_gates(self):
        fixture = fixtures.StockCatalogTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.publish()
        before = hashlib.sha256(fixture.path.read_bytes()).hexdigest()
        with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            report = e.apply_candidates(e.preview(fixture.path, at=AT, synthetic=True))
        self.assertEqual(hashlib.sha256(fixture.path.read_bytes()).hexdigest(), before)
        self.assertTrue(report['provenance']['hash_unchanged'])
        self.assertEqual(sum(report['candidate_policy']['counts'].values()), 5)
        for row in report['records']:
            self.assertTrue(row['candidate_eligibility']['evidence']['run_id'])
        row = sample(sector_state='proposed')
        source = dict(at=AT, records=[row])
        self.assertEqual(e.apply_candidates(source)['candidate_policy']['after_count'], 1)
        self.assertEqual(e.sector_turnover([dict(stock_code='000001', turnover_won=100)], source)['unique_turnover_won'], '0')


if __name__ == '__main__':
    unittest.main()
