"""Synthetic saved-response integrity and documented conversion tests."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from kiwoom_collector import c15_turnover_audit as audit, turnover_rank

CONTRACT=json.loads((Path(__file__).resolve().parents[1]/'config/c15-turnover-contract.json').read_text(encoding='utf8'))


class AmountTests(unittest.TestCase):
    def test_hts_example_is_arithmetic_not_rest_empirical_proof(self):
        r=audit.documented_amount('306023',CONTRACT)
        self.assertEqual((r['raw_value'],r['documented_won'],r['documented_eok']),('306023','306023000000','3060.23'))
        self.assertFalse(r['scoring_allowed'])

    def test_three_recorded_raw_examples_arithmetic(self):
        for value,won,eok in [('788193','788193000000','7881.93'),('767341','767341000000','7673.41'),('355352','355352000000','3553.52')]:
            r=audit.documented_amount(value,CONTRACT)
            self.assertEqual((r['documented_won'],r['documented_eok']),(won,eok))

    def test_fractional_large_values_are_not_rounded_or_changed(self):
        self.assertEqual(audit.documented_amount('1234.56',CONTRACT)['documented_won'],'1234560000')
        raw='123456789012345678901234567890123456789'
        self.assertEqual(audit.documented_amount(raw,CONTRACT)['documented_won'],raw+'000000')

    def test_bad_amounts_and_wrong_contract_do_not_convert(self):
        for raw in ('306,023','-1','NaN',None):
            self.assertIsNone(audit.documented_amount(raw,CONTRACT)['documented_won'])
        self.assertIsNone(audit.documented_amount('1',dict(CONTRACT,unit='KRW'))['documented_won'])

    def test_hts_or_metadata_flags_alone_do_not_enable_scores(self):
        self.assertFalse(audit.documented_amount('1',dict(CONTRACT,runtime_unit_verified=True))['scoring_allowed'])

    def fixture(self):
        folder=self.enterContext(tempfile.TemporaryDirectory());path=Path(folder)/'turnover.db'
        raw=dict(stk_cd='000001',stk_nm='합성종목',now_rank='1',cur_prc='+100',flu_rt='+2',now_trde_qty='10',trde_prica='1234.56')
        normalized=turnover_rank.normalize(raw,turnover_rank.Profile())['normalized']
        with closing(sqlite3.connect(path)) as db,db:
            db.execute('CREATE TABLE turnover_pages(run_id,page_no,response_json)')
            db.execute('CREATE TABLE turnover_rows(run_id,page_no,row_no,stock_code,raw_json,normalized_json)')
            db.execute('INSERT INTO turnover_pages VALUES(?,?,?)',('run',1,json.dumps(dict(trde_prica_upper=[raw]))))
            db.execute('INSERT INTO turnover_rows VALUES(?,?,?,?,?,?)',('run',1,1,'000001',json.dumps(raw),json.dumps(normalized)))
        return path,dict(id='run',snapshot_sha256='synthetic',rows=[dict(stock_code='000001',raw_json=json.dumps(raw))])

    def test_saved_page_row_snapshot_match_and_decimal_conversion(self):
        path,snapshot=self.fixture();before=path.read_bytes();r=audit.audit(path,snapshot,CONTRACT)
        self.assertTrue(r['storage_consistent']);self.assertEqual(r['checked_rows'],1)
        self.assertFalse(r['wire_bytes_preserved']);self.assertFalse(r['amount_score_enabled'])
        self.assertEqual(path.read_bytes(),before)

    def test_page_amount_or_extra_row_mismatch_is_explicit(self):
        for response in ([dict(stk_cd='000001',trde_prica='1234')],[{},{}]):
            path,snapshot=self.fixture()
            with closing(sqlite3.connect(path)) as db,db:
                db.execute('UPDATE turnover_pages SET response_json=?',(json.dumps(dict(trde_prica_upper=response)),))
            self.assertFalse(audit.audit(path,snapshot,CONTRACT)['storage_consistent'])

    def test_missing_snapshot_does_not_read_database(self):
        self.assertEqual(audit.audit('not-a-db',None,CONTRACT)['status'],'missing')


if __name__=='__main__':unittest.main()
