import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from contextlib import closing
from kiwoom_collector import krx_backfill as backfill
from kiwoom_collector.krx_adr_prepare import rolling

class ResumeTests(unittest.TestCase):
    def test_received_receipt_survives_restart_without_fetch(self):
        with tempfile.TemporaryDirectory() as t, patch.object(backfill,'ROOT',Path(t).resolve()):
            root=Path(t).resolve()/'run'
            r=dict(service='stk_bydd_trd',basis_date='2026-10-01',source_kind='krx_live',http_status=200,raw_response={'OutBlock_1':[]})
            calls=[]
            first=backfill.Backfill(None,root,lambda *a:calls.append(a) or r)
            first.receipt('2026-10-01','stk_bydd_trd')
            second=backfill.Backfill(None,root,lambda *a:self.fail('duplicate request'))
            self.assertEqual(second.receipt('2026-10-01','stk_bydd_trd')[0],r)
            self.assertEqual(len(calls),1)

    def test_quota_response_stops_without_retry(self):
        with tempfile.TemporaryDirectory() as t, patch.object(backfill,'ROOT',Path(t).resolve()):
            calls=[]
            r=dict(service='stk_bydd_trd',basis_date='2026-10-01',source_kind='krx_live',http_status=429,status='quota_limited')
            runner=backfill.Backfill(None,Path(t).resolve()/'run',lambda *a:calls.append(a) or r)
            with self.assertRaises(ValueError):runner.receipt('2026-10-01','stk_bydd_trd')
            self.assertEqual(len(calls),1)

    def test_interrupted_day_persisted(self):
        with tempfile.TemporaryDirectory() as t, patch.object(backfill,'ROOT',Path(t).resolve()):
            runner=backfill.Backfill(None,Path(t).resolve()/'run',None)
            with patch.object(runner,'collect_day',side_effect=ValueError('stop')):
                with self.assertRaises(ValueError):runner.run('2026-10-01','2026-10-01')
            with closing(runner.connection()) as db:
                self.assertEqual(db.execute('select state from dates').fetchone()[0],'interrupted_or_failed')

    def test_adr_ratio_of_sums_and_warmup(self):
        counts=[(str(i),1,1,9) for i in range(19)]+[('last',100,10,0)]
        rows=list(rolling(counts))
        self.assertIsNone(rows[18][-1])
        self.assertEqual(rows[-1][-3:-1],(119,29))
        from decimal import Decimal
        self.assertEqual(Decimal(rows[-1][-1]),Decimal(100)*119/29)

    def test_zero_denominator_and_sliding_window(self):
        rows=list(rolling([(str(i),1,0,0) for i in range(21)]))
        self.assertIsNone(rows[-1][-1])
        self.assertEqual(rows[-1][-3],20)

if __name__=='__main__':unittest.main()
