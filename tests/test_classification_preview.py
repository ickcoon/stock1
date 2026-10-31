"""Synthetic-only preview checks, including source hash and network exclusion."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from kiwoom_collector import classification_preview as p, master_data as m
import test_stock_catalog as fixtures

AT = '2026-10-30T16:02:00+09:00'


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.StockCatalogTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def check_preview(self):
        path = self.fixture.path
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            report = p.preview(path, at=AT, synthetic=True)
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertTrue(report['provenance']['hash_unchanged'])
        self.assertFalse(any(Path(str(path)+s).exists() for s in ('-wal', '-shm', '-journal')))
        total = report['unique_stocks']
        self.assertEqual(sum(report['markets'].values()), total)
        self.assertEqual(sum(sum(v.values()) for v in report['product_types'].values()), total)
        self.assertEqual(sum(report['sector_states'].values()), total)
        rows = [r for r in report['records'] if not r.get('excluded_from_counts')]
        self.assertEqual(len({r['stock_code'] for r in rows}), total)
        for state in ('confirmed', 'proposed'):
            self.assertEqual(sum(v[state] for v in report['representative_sectors'].values()),
                             report['sector_states'][state])
        return report

    def test_independent_market_type_and_sector_counts(self):
        f = self.fixture
        doc = json.loads((fixtures.FIXTURE.parent/'ka10099_mixed_synthetic.json').read_text(encoding='utf-8'))
        f.publish(f.dataset(doc))
        report = self.check_preview()
        self.assertEqual(report['markets'], {'KOSPI': 1, 'KOSDAQ': 2, 'UNKNOWN': 1})
        unknown = next(r for r in report['records'] if r['market'] == 'UNKNOWN')
        self.assertFalse(unknown['type_verified'])
        self.assertEqual(unknown['stock_state'], 'catalog_market_unclassified')
        self.assertEqual(report['unique_stocks'], 4)
        self.assertEqual(sum(report['sector_states'].values()), 4)
        self.assertEqual(len(report['taxonomy']['c10']), 16)
        self.assertEqual(len(report['representative_sectors']), 15)

    def test_proposals_never_become_confirmed(self):
        f = self.fixture
        for rule in f.rules['rules']:
            rule['reviewed'] = False
        f.rules['security_reviews'] = {}
        f.publish()
        report = self.check_preview()
        self.assertEqual(report['sector_states']['confirmed'], 0)
        self.assertGreater(report['sector_states']['proposed'], 0)
        self.assertEqual(sum(v['verified'] for v in report['product_types'].values()), 0)
        self.assertGreater(report['product_types']['unknown']['unverified'], 0)

    def test_duplicate_primary_and_theme_tags(self):
        f = self.fixture
        f.publish()
        with m.MasterStore(f.path) as store:
            store.put_sector('fixture_theme', 'SYNTHETIC theme', kind='theme', source='fixture', updated_at=AT)
            for sector in ('industry_02', 'fixture_theme'):
                store.put_tag('000001', sector, source='fixture', evidence='synthetic only',
                    updated_at=AT, registered_at=AT, valid_from=AT,
                    is_primary=sector != 'fixture_theme', status='verified')
        report = self.check_preview()
        row = next(r for r in report['records'] if r['stock_code'] == '000001')
        self.assertEqual(row['sector_state'], 'conflict')
        self.assertTrue(row['duplicate_primary'])
        self.assertGreaterEqual(report['multiple_tags_count'], 1)
        self.assertEqual(report['duplicate_primary_count'], 1)

    def test_c10_only_cli_and_scope_rejection(self):
        f = self.fixture
        with m.MasterStore(f.path) as store:
            store.put_stock('000001', 'SYNTHETIC', 'UNKNOWN', 'unknown', source='fixture',
                            updated_at=AT, registered_at=AT)
        report = self.check_preview()
        self.assertEqual(report['sector_states']['unclassified'], 1)
        self.assertEqual(report['product_types']['unknown']['unverified'], 1)
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(p.main(['--db', str(f.path), '--at', AT, '--synthetic']), 0)
        self.assertEqual(json.loads(out.getvalue())['markets']['UNKNOWN'], 1)
        with self.assertRaisesRegex(ValueError, 'master_source_scope_mismatch'):
            p.preview(f.path, at=AT)
        with self.assertRaises(ValueError):
            p.preview(f.path, at='2026-10-30')

    def test_sector_confirmation_is_independent_of_unknown_product(self):
        f = self.fixture
        f.rules['security_reviews'] = {}
        f.publish()
        report = self.check_preview()
        self.assertEqual(report['sector_states'], dict(confirmed=3, proposed=0, unclassified=2, conflict=0))
        row = next(r for r in report['records'] if r['stock_code'] == '000001')
        self.assertEqual(row['security_type'], 'unknown')
        self.assertFalse(row['type_verified'])
        self.assertEqual(row['sector_state'], 'confirmed')
        self.assertEqual(row['analysis_classification_state'], 'catalog_security_review')

    def test_conflicting_proposals_and_rejected_expired_tags(self):
        f = self.fixture
        for rule in f.rules['rules']:
            rule['reviewed'] = False
        f.publish()
        with m.MasterStore(f.path) as store:
            store.put_tag('000001', 'industry_02', source='fixture', evidence='synthetic candidate',
                          updated_at=AT, registered_at=AT, valid_from=AT, status='proposed')
            store.put_tag('000003', 'industry_01', source='fixture', evidence='synthetic rejection',
                          updated_at=AT, registered_at=AT, valid_from=AT, status='rejected', is_primary=True)
            store.put_tag('000003', 'industry_02', source='fixture', evidence='synthetic expired',
                          updated_at='2026-10-29T08:00:00+09:00', registered_at=AT,
                          valid_from='2026-10-29T08:00:00+09:00', valid_to=AT,
                          status='verified', is_primary=True)
        report = self.check_preview()
        rows = {r['stock_code']: r for r in report['records']}
        self.assertEqual(rows['000001']['sector_state'], 'conflict')
        self.assertFalse(rows['000001']['duplicate_primary'])
        self.assertEqual(rows['000003']['sector_state'], 'unclassified')
        self.assertEqual(report['sector_states']['confirmed'], 0)

    def test_c10_bridge_preserves_evidence_and_holds_ambiguous_labels(self):
        from kiwoom_collector.sector_taxonomy import link
        f = self.fixture
        with m.MasterStore(f.path) as store:
            for index, name in enumerate(m.BROAD_SECTORS, 1):
                code = f'{index:06}'
                store.put_stock(code, 'SYNTHETIC ONLY', 'UNKNOWN', 'unknown', source='fixture',
                                updated_at=AT, registered_at=AT)
                store.put_tag(code, f'industry_{index:02}', source='synthetic reviewed source',
                              evidence='synthetic verified evidence', updated_at=AT, registered_at=AT,
                              valid_from=AT, status='verified', is_primary=True)
        report = self.check_preview()
        self.assertEqual(report['sector_states'], dict(confirmed=14, proposed=0, unclassified=2, conflict=0))
        for row, original in zip(report['records'], m.BROAD_SECTORS):
            self.assertEqual(row['representative_sector'], link(original)['sector'])
            self.assertEqual(row['tags'][0]['sector'], original)
            self.assertEqual(row['tags'][0]['evidence'], 'synthetic verified evidence')
            self.assertEqual(row['tags'][0]['registered_at'], AT)
            self.assertTrue(row['tags'][0]['record_hash'])
        self.assertEqual(report['representative_sectors']['원전·전력']['confirmed'], 2)

    def test_bridge_does_not_promote_proposals_or_merge_duplicate_primaries(self):
        f = self.fixture
        with m.MasterStore(f.path) as store:
            store.put_stock('000001', 'SYNTHETIC ONLY', 'UNKNOWN', 'unknown', source='fixture',
                            updated_at=AT, registered_at=AT)
            for index in (4, 5):  # Both labels connect to power, but two primaries still conflict.
                store.put_tag('000001', f'industry_{index:02}', source='fixture', evidence='synthetic only',
                              updated_at=AT, registered_at=AT, valid_from=AT, status='verified', is_primary=True)
            store.put_stock('000002', 'SYNTHETIC ONLY', 'UNKNOWN', 'unknown', source='fixture',
                            updated_at=AT, registered_at=AT)
            store.put_tag('000002', 'industry_04', source='fixture', evidence='synthetic proposal',
                          updated_at=AT, registered_at=AT, valid_from=AT, status='proposed')
        report = self.check_preview()
        self.assertEqual(report['sector_states'], dict(confirmed=0, proposed=1, unclassified=0, conflict=1))
        self.assertEqual(report['representative_sectors']['원전·전력']['proposed'], 1)
