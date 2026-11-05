"""AP-1 synthetic store tests; no network or operational inputs."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_collector import krx_daily as daily, krx_openapi as krx, master_data as master
from kiwoom_collector.krx_master import sha

DAY = '2026-11-02'
AT = '2026-11-03T10:00:00+09:00'


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.scope = patch.object(daily, 'ROOT', self.root/'diagnostics/krx-daily')
        self.scope.start()
        self.addCleanup(self.scope.stop)
        self.store = daily.DailyStore(daily.ROOT/'fixture.db', synthetic=True)
        fixture = json.loads((Path(__file__).parent/'fixtures/krx_an_synthetic.json').read_text(encoding='utf8'))
        self.body = fixture['responses']['stk_bydd_trd']
        base = fixture['responses']['stk_isu_base_info']
        self.masters = krx.parse(base, service='stk_isu_base_info', bas_dd='20261102',
                                 source_kind='synthetic_fixture')['records']
        self.master_hash = master.digest(base)

    def receipt(self, **changes):
        r = dict(service='stk_bydd_trd', basis_date=DAY, source_kind='synthetic_fixture',
                 http_status=200, response_received_at=AT, raw_response=deepcopy(self.body))
        r.update(changes)
        return r

    def ingest(self, receipt=None):
        return self.store.ingest(receipt or self.receipt(), masters=self.masters, master_body_hash=self.master_hash)

    def bars(self):
        with daily.ao.read_db(self.store.path) as db:
            return [dict(r) for r in db.execute('SELECT * FROM daily_latest')]

    def test_roundtrip_exact_raw_numbers_and_dated_identity(self):
        self.assertEqual(self.ingest()['state'], 'ready')
        row = self.bars()[0]
        self.assertEqual(row['stock_code'], '000001')
        self.assertEqual(row['standard_code'], self.masters[0]['standard_code'])
        self.assertEqual(row['master_basis_date'], DAY)
        self.assertEqual(row['turnover_raw'], self.body['OutBlock_1'][0]['ACC_TRDVAL'].replace(',', ''))
        with daily.ao.read_db(self.store.path) as db:
            self.assertEqual(json.loads(db.execute('SELECT raw_json FROM raw_rows').fetchone()[0]), self.body['OutBlock_1'][0])

    def test_amounts_not_converted_without_unit_evidence(self):
        self.ingest()
        row = self.bars()[0]
        self.assertIsNone(row['turnover_won'])
        self.assertIsNone(row['market_cap_won'])
        self.assertEqual(self.store.report()['amount_units'], 'unverified_no_conversion')

    def test_duplicate_reexecution_is_byte_stable(self):
        self.ingest()
        before = sha(self.store.path)
        self.assertEqual(self.ingest()['state'], 'already_imported')
        self.assertEqual(before, sha(self.store.path))
        self.assertEqual(len(self.bars()), 1)

    def test_revised_response_retains_old_revision(self):
        self.ingest()
        changed = self.receipt(response_received_at='2026-11-03T10:01:00+09:00')
        changed['raw_response']['OutBlock_1'][0]['ISU_NM'] = '다른명칭'
        self.assertEqual(self.ingest(changed)['state'], 'ready')
        self.assertIsNone(self.bars()[0]['stock_code'])
        with daily.ao.read_db(self.store.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bars').fetchone()[0], 2)
        self.assertEqual(len(self.bars()), 1)

    def test_stale_revision_does_not_replace_newer(self):
        self.ingest()
        old = self.receipt(response_received_at='2026-11-03T09:00:00+09:00')
        old['raw_response']['OutBlock_1'][0]['ISU_NM'] = '과거명칭'
        self.assertEqual(self.ingest(old)['state'], 'stale_receipt_preserved_history')
        self.assertEqual(self.bars()[0]['name'], self.body['OutBlock_1'][0]['ISU_NM'])
        self.assertEqual(self.store.report()['missing_sessions'], [])

    def test_absent_numeric_field_is_null_not_zero_and_raw_is_not_patched(self):
        r = self.receipt()
        del r['raw_response']['OutBlock_1'][0]['ACC_TRDVAL']
        r['raw_response']['OutBlock_1'][0]['MKTCAP'] = None
        self.assertEqual(self.ingest(r)['state'], 'ready')
        row = self.bars()[0]
        self.assertIsNone(row['turnover_raw'])
        self.assertIsNone(row['market_cap_raw'])
        with daily.ao.read_db(self.store.path) as db:
            raw = json.loads(db.execute('SELECT raw_json FROM raw_rows').fetchone()[0])
            self.assertNotIn('ACC_TRDVAL', raw)
            self.assertIsNone(raw['MKTCAP'])

    def test_bad_ohlc_quarantines_without_overwriting_current(self):
        self.ingest()
        bad = self.receipt()
        bad['raw_response']['OutBlock_1'][0]['TDD_HGPRC'] = '1'
        self.assertEqual(self.ingest(bad)['state'], 'quarantined_invalid_rows')
        self.assertEqual(len(self.bars()), 1)
        self.assertEqual(self.store.report()['missing_sessions'], [])
        self.assertEqual(len(self.store.report()['invalid_raw_rows']), 1)

    def test_duplicate_identifier_quarantined(self):
        r = self.receipt()
        r['raw_response']['OutBlock_1'] *= 2
        self.assertEqual(self.ingest(r)['state'], 'quarantined_invalid_rows')
        self.assertEqual(self.bars(), [])

    def test_http_error_empty_and_missing_sessions(self):
        for changes, state in ((dict(http_status=429, raw_response={'resp_msg':'quota'}), 'request_or_contract_failed'),
                               (dict(raw_response={'OutBlock_1':[]}), 'empty_unclassified')):
            self.assertEqual(self.ingest(self.receipt(**changes))['state'], state)
        self.assertEqual(self.bars(), [])
        self.store.record_unattempted([DAY])
        self.assertEqual(len(self.store.report()['missing_sessions']), 2)

    def test_wrong_response_date_is_quarantined(self):
        r = self.receipt()
        r['raw_response']['OutBlock_1'][0]['BAS_DD'] = '20261030'
        self.assertEqual(self.ingest(r)['state'], 'quarantined_invalid_rows')

    def test_dated_master_and_origin_boundaries(self):
        bad = deepcopy(self.masters)
        bad[0]['basis_date'] = '2026-10-30'
        with self.assertRaisesRegex(ValueError, 'same_date_market'):
            self.store.ingest(self.receipt(), masters=bad, master_body_hash=self.master_hash)
        with self.assertRaisesRegex(ValueError, 'dated_master_hash'):
            self.store.ingest(self.receipt(), masters=self.masters, master_body_hash='wrong')
        with self.assertRaisesRegex(ValueError, 'origin_mismatch'):
            self.store.ingest(self.receipt(source_kind='krx_live'))

    def test_late_identity_evidence_can_reprocess_same_receipt(self):
        r = self.receipt()
        self.store.ingest(r)
        self.assertIsNone(self.bars()[0]['stock_code'])
        self.ingest(r)
        self.assertEqual(self.bars()[0]['stock_code'], '000001')

    def test_zero_volume_is_not_proof_of_suspension(self):
        r = self.receipt()
        raw = r['raw_response']['OutBlock_1'][0]
        for key in ('TDD_OPNPRC','TDD_HGPRC','TDD_LWPRC','ACC_TRDVOL','CMPPREVDD_PRC','FLUC_RT'):
            raw[key] = '0'
        self.ingest(r)
        row = self.bars()[0]
        self.assertEqual(row['direction'], 'flat')
        self.assertEqual(row['trading_state'], 'zero_volume_unclassified')
        self.assertIsNone(row['halt_status'])
        self.assertEqual(self.store.report()['market_days'][0]['adr_common_candidate'], 0)

    def test_calendar_is_candidates_not_asserted_exchange_sessions(self):
        self.assertEqual(daily.candidate_sessions('2026-10-26','2026-11-01'),
                         ['2026-10-26','2026-10-27','2026-10-28','2026-10-29','2026-10-30'])
        holidays = self.root/'holidays.txt'
        holidays.write_text('2026-10-28\n', encoding='utf8')
        self.assertNotIn('2026-10-28', daily.candidate_sessions('2026-10-26','2026-10-30', holidays=holidays))

    def test_operational_path_and_existing_arbitrary_file_never_written(self):
        source = self.root/'operating.db'
        source.write_bytes(b'protected sentinel')
        with self.assertRaisesRegex(ValueError, 'independent_krx_daily'):
            daily.DailyStore(source)
        self.assertEqual(source.read_bytes(), b'protected sentinel')
        with self.assertRaisesRegex(ValueError, 'daily_schema_or_origin'):
            daily.DailyStore(self.store.path, synthetic=False)

    def test_integrity(self):
        self.ingest()
        report = self.store.report()
        self.assertEqual(report['integrity_check'], ['ok'])
        self.assertEqual(report['foreign_key_violations'], [])

    def test_previous_close_mismatch_is_preserved_as_review_not_rewritten(self):
        self.ingest()
        r = self.receipt(basis_date='2026-11-03')
        raw = r['raw_response']['OutBlock_1'][0]
        raw.update(BAS_DD='20261103', CMPPREVDD_PRC='1')
        bases = deepcopy(self.masters)
        bases[0]['basis_date'] = '2026-11-03'
        self.store.ingest(r, masters=bases, master_body_hash=self.master_hash)
        anomalies = self.store.report()['previous_close_anomalies']
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]['reported_change'], '1')
        self.assertEqual(anomalies[0]['computed_change'], '0')

    def test_missing_session_is_not_treated_as_adjacent_previous_session(self):
        self.ingest()
        self.store.record_unattempted(['2026-11-03'])
        r = self.receipt(basis_date='2026-11-04')
        r['raw_response']['OutBlock_1'][0]['BAS_DD'] = '20261104'
        bases = deepcopy(self.masters)
        bases[0]['basis_date'] = '2026-11-04'
        self.store.ingest(r, masters=bases, master_body_hash=self.master_hash)
        self.assertEqual(self.store.report()['market_days'][-1]['previous_close_compared'], 0)


if __name__ == '__main__':
    unittest.main()
