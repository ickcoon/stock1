"""Synthetic AO tests. No KRX requests, credentials or operational writes."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_collector import krx_master as ao, master_data as master

DAY = '2026-11-02'
AT = '2026-11-03T12:00:00+09:00'


def security(code, full, short=None, *, kind='보통주', group='주권'):
    return dict(stock_code=code, standard_code='KR7' + code + '001', official_name=full,
                abbreviated_name=short or full, market='KOSPI', basis_date=DAY,
                stock_type_raw=kind, security_group_raw=group)


class NameTests(unittest.TestCase):
    def match(self, name, records, **kwargs):
        return ao.match_names([name], records, basis_date=DAY, **kwargs)[0]

    def test_full_and_abbreviation_distinct_stages(self):
        records = [security('005930', '삼성전자보통주', '삼성전자')]
        self.assertEqual(self.match('삼성전자보통주', records)['state'], 'official_full_name')
        self.assertEqual(self.match('삼성전자', records)['state'], 'official_abbreviation')

    def test_common_preferred_never_collapsed(self):
        records = [security('005930', '삼성전자보통주', '삼성전자'),
                   security('005935', '삼성전자우선주', '삼성전자우', kind='구형우선주')]
        self.assertEqual(self.match('삼성전자', records)['stock_code'], '005930')
        self.assertEqual(self.match('삼성전자우', records)['stock_code'], '005935')
        self.assertEqual(self.match('삼성전자우B', records)['state'], 'unmatched')

    def test_no_prefix_or_similarity_shortcuts(self):
        records = [security('000880', '한화'), security('012450', '한화에어로스페이스'),
                   security('005380', '현대차'), security('307950', '현대오토에버')]
        self.assertEqual(self.match('한화', records)['stock_code'], '000880')
        self.assertEqual(self.match('현대차', records)['stock_code'], '005380')
        for name in ('한화에어', '현대오토', '없는종목', '현대차우'):
            self.assertFalse(self.match(name, records)['confirmed'])

    def test_duplicate_names_and_cross_namespace_ambiguity(self):
        records = [security('000001', '중복', 'A'), security('000002', '중복', 'B')]
        self.assertEqual(self.match('중복', records)['state'], 'multiple_candidates')
        records = [security('000001', '한화', 'A'), security('000002', '다른회사', '한화')]
        self.assertEqual(self.match('한화', records)['state'], 'multiple_candidates')

    def test_only_whitespace_normalization_and_collisions(self):
        records = [security('000001', 'LS ELECTRIC')]
        r = self.match(' LS　ELECTRIC ', records)
        self.assertTrue(r['confirmed'])
        self.assertEqual(r['normalization'], 'NFC_and_whitespace_only')
        self.assertFalse(self.match('LSELECTRIC우', records)['confirmed'])
        self.assertFalse(self.match('LS-ELECTRIC', records)['confirmed'])
        records.append(security('000002', 'LSELECTRIC'))
        self.assertEqual(self.match('LS ELECTRIC  ', records)['state'], 'multiple_candidates')

    def test_alias_requires_evidence_and_date_and_exact_target(self):
        records = [security('000001', '정식명')]
        review = dict(raw_name='약칭', stock_code='000001', canonical_name='정식명', market='KOSPI',
                      kind='alias', verified=True, evidence_url='https://example.test/review',
                      valid_from='2026-01-01', reviewed_at=AT)
        self.assertEqual(self.match('약칭', records, reviews=[review])['state'], 'verified_alias')
        for change in ({'verified': False}, {'evidence_url': ''}, {'valid_from': '2026-11-03'},
                       {'valid_to': DAY}, {'canonical_name': '다른회사'}, {'market': 'KOSDAQ'}):
            self.assertFalse(self.match('약칭', records, reviews=[dict(review, **change)])['confirmed'])

    def test_historical_name_requires_covering_source_dates(self):
        records = [security('000001', '새이름')]
        review = dict(raw_name='옛이름', stock_code='000001', canonical_name='새이름', market='KOSPI',
                      kind='name_history', verified=True, evidence_url='https://example.test/history',
                      valid_from='2025-01-01', valid_to='2026-05-01', reviewed_at=AT)
        self.assertFalse(self.match('옛이름', records)['confirmed'])
        self.assertFalse(self.match('옛이름', records, reviews=[review])['confirmed'])
        r = self.match('옛이름', records, reviews=[review], observation_dates={'옛이름': ['2026-03-01']})
        self.assertEqual(r['state'], 'verified_name_history')
        self.assertFalse(r['historical_identity_verified'])
        self.assertFalse(self.match('옛이름', records, reviews=[review],
                                   observation_dates={'옛이름': ['2026-03-01', '2026-06-01']})['confirmed'])

    def test_name_denominator_differs_from_relation_denominator(self):
        records = [security('000001', '이름A')]
        matches = ao.match_names(['이름A', '이름B'], records, basis_date=DAY)
        rels = [dict(stock_name_raw='이름A', theme_label=t) for t in ('AI', '로봇')]
        rels.append(dict(stock_name_raw='이름B', theme_label='AI'))
        stats = ao.statistics(records, matches, rels)
        self.assertEqual(stats['confirmed_name_percent'], 50)
        self.assertEqual(stats['confirmed_relation_percent'], 66.6667)
        self.assertEqual(stats['multi_theme_names'], 1)

    def test_official_types_and_uncertain_products(self):
        for group, kind, expected in [('주권', '보통주', 'common_stock'),
                ('주권', '구형우선주', 'preferred_stock'), ('부동산투자회사', '보통주', 'REIT'),
                ('ETF', '보통주', 'ETF'), ('ETN', '보통주', 'ETN'),
                ('기업인수목적회사', '보통주', 'SPAC'), ('주권', '종류주권', 'UNKNOWN')]:
            r = ao.classify(security('0000A0', '합성', group=group, kind=kind))
            self.assertEqual(r['product_type'], expected)
        r = ao.classify(security('0000A0', '합성기업인수목적', '합성스팩'))
        self.assertEqual(r['product_type'], 'UNKNOWN')
        self.assertEqual(r['hint'], 'SPAC')
        self.assertFalse(r['type_verified'])


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.scope = patch.multiple(master, PROJECT=self.root, MASTER_ROOT=self.root / 'diagnostics' / 'master')
        self.scope.start()
        self.addCleanup(self.scope.stop)
        fixture = json.loads((Path(__file__).parent / 'fixtures/krx_an_synthetic.json').read_text(encoding='utf8'))
        self.records, self.provenance, self.paths = [], [], []
        for service in ('stk_isu_base_info', 'ksq_isu_base_info'):
            receipt = dict(service=service, source_kind='synthetic_fixture', http_status=200, basis_date=DAY,
                           request_started_at=AT, response_received_at=AT, raw_response=fixture['responses'][service])
            path = self.root / (service + '.json')
            path.write_text(json.dumps(receipt), encoding='utf8')
            rows, origin = ao.load_snapshot(path, service=service, basis_date=DAY, synthetic=True)
            self.paths.append(path)
            self.records.extend(rows)
            self.provenance.append(origin)
        first = self.records[0]['abbreviated_name']
        self.loaded = dict(records=[], metadata=dict(master_stock_names=[dict(stock_name_raw=first),
                          dict(stock_name_raw='없는종목')], master_stock_theme=[
                              dict(stock_name_raw=first, theme_label=t) for t in ('AI', '로봇')]))
        self.inspection = dict(sha256='fixture-theme-hash')

    def build(self, **changes):
        args = dict(records=self.records, provenance=self.provenance, loaded=self.loaded,
                    inspection=self.inspection, basis_date=DAY, synthetic=True)
        args.update(changes)
        return ao.publish('fixture', **args)

    def test_base_schema_reused_codes_raw_provenance_and_no_live_promotion(self):
        result = self.build()
        with ao.read_db(result['path']) as db:
            codes = [r[0] for r in db.execute('SELECT stock_code FROM krx_securities ORDER BY stock_code')]
            self.assertEqual(codes, ['000001', '0000A0'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM stock_history').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT SUM(verified) FROM stock_master').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM stock_sector').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM external_theme_relations').fetchone()[0], 2)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            raw = json.loads(db.execute("SELECT raw_json FROM krx_securities WHERE stock_code='000001'").fetchone()[0])
            self.assertEqual(raw, self.records[0]['raw'])

    def test_idempotent_even_with_new_receipt_metadata(self):
        result = self.build()
        before = ao.sha(result['path'])
        provenance = deepcopy(self.provenance)
        provenance[0].update(received_at='2026-11-03T12:01:00+09:00', source_path='another-copy.json')
        second = self.build(records=list(reversed(self.records)), provenance=provenance)
        self.assertEqual(second['state'], 'already_imported')
        self.assertEqual(ao.sha(result['path']), before)

    def test_existing_changed_snapshot_never_overwritten(self):
        result = self.build()
        before = ao.sha(result['path'])
        records = deepcopy(self.records)
        records[0]['official_name'] = '변경'
        with self.assertRaisesRegex(ValueError, 'existing_snapshot_differs'):
            self.build(records=records)
        self.assertEqual(before, ao.sha(result['path']))

    def test_duplicate_code_and_standard_code_rejected(self):
        records = deepcopy(self.records)
        records[1]['stock_code'] = records[0]['stock_code']
        with self.assertRaisesRegex(ValueError, 'duplicate_short_code'):
            self.build(records=records)
        records = deepcopy(self.records)
        records[1]['standard_code'] = records[0]['standard_code']
        with self.assertRaisesRegex(ValueError, 'duplicate_standard_code'):
            self.build(records=records)

    def test_basis_date_and_synthetic_live_boundaries(self):
        records = deepcopy(self.records)
        records[0]['basis_date'] = '2026-10-30'
        with self.assertRaisesRegex(ValueError, 'same_date_origin'):
            self.build(records=records)
        with self.assertRaisesRegex(ValueError, 'same_date_origin'):
            self.build(synthetic=False)
        with self.assertRaisesRegex(ValueError, 'snapshot_scope_or_origin'):
            ao.load_snapshot(self.paths[0], service='stk_isu_base_info', basis_date=DAY)

    def test_duplicate_raw_names_relations_rejected(self):
        loaded = deepcopy(self.loaded)
        loaded['metadata']['master_stock_names'] *= 2
        with self.assertRaisesRegex(ValueError, 'duplicate_raw_master_names'):
            self.build(loaded=loaded)
        loaded = deepcopy(self.loaded)
        loaded['metadata']['master_stock_theme'] *= 2
        with self.assertRaisesRegex(ValueError, 'duplicate_raw_relations'):
            self.build(loaded=loaded)

    def test_writer_cannot_target_operating_db(self):
        path = self.root / 'operating.db'
        path.write_bytes(b'original protected sentinel')
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'invalid_master_scope'):
            ao.publish('../operating', self.records, self.provenance, self.loaded,
                       self.inspection, basis_date=DAY, synthetic=True)
        with self.assertRaisesRegex(ValueError, 'development_master_directory'):
            master.MasterStore(path)
        self.assertEqual(before, path.read_bytes())

    def test_export_preserves_multiple_themes_and_raw_strings(self):
        result = self.build()
        out = self.root / 'review'
        ao.export_review(result['path'], out)
        self.assertEqual(len(json.loads((out / 'relations.json').read_text(encoding='utf8'))), 2)
        self.assertEqual(len(json.loads((out / 'alphanumeric-codes.json').read_text(encoding='utf8'))), 1)
        self.assertIn('000001', (out / 'matches.csv').read_text(encoding='utf-8-sig'))


if __name__ == '__main__':
    unittest.main()
