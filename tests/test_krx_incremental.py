"""Synthetic-only AS tests. No API keys, live network, Collector or production DB."""
from copy import deepcopy
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_collector import krx_incremental as inc
from kiwoom_collector import krx_daily as daily, krx_backfill as backfill
from kiwoom_collector import krx_adr as adr, krx_openapi as api


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        scope=patch.object(daily,'ROOT',self.root/'daily');scope.start();self.addCleanup(scope.stop)
        self.store=daily.DailyStore(daily.ROOT/'fixture.db',synthetic=True)
        self.checkpoint=self.root/'progress.db'
        with closing(sqlite3.connect(self.checkpoint)) as db, db:
            db.executescript('CREATE TABLE backfill_meta(version INTEGER);INSERT INTO backfill_meta VALUES(1);'
                'CREATE TABLE dates(day TEXT PRIMARY KEY,state TEXT,details_json TEXT);'
                'CREATE TABLE as_meta(key TEXT PRIMARY KEY,value TEXT);'
                "INSERT INTO as_meta VALUES('bootstrap_last_day','2026-10-30');")
        self.fixture=json.loads((Path(__file__).parent/'fixtures/krx_an_synthetic.json').read_text(encoding='utf8'))['responses']
        self.calls=[];self.overrides={};self.seq=0

    def fetch(self,day,service):
        self.calls.append((day,service));self.seq+=1
        body=deepcopy(self.fixture[service])
        if service.endswith('bydd_trd'):
            for row in body['OutBlock_1']:row['BAS_DD']=day.replace('-','')
        receipt=dict(service=service,basis_date=day,source_kind='synthetic_fixture',http_status=200,
            request_started_at='2026-11-10T10:00:00+09:00',response_received_at='2026-11-10T10:01:00+09:00',raw_response=body)
        update=self.overrides.get(service)
        if callable(update):update(receipt)
        elif update:receipt.update(update)
        path=self.root/f'receipt-{self.seq}.json'
        path.write_text(json.dumps(receipt),encoding='utf8')
        return receipt,path

    def runner(self):
        return inc.Updater(self.store,self.checkpoint,self.root/('run-'+str(self.seq)),self.fetch,
                           now=datetime.fromisoformat('2026-11-04T12:00:00+09:00'))

    def count(self,table='bars'):
        with closing(sqlite3.connect(self.store.path)) as db, db:return db.execute('select count(*) from '+table).fetchone()[0]

    def test_new_day_and_rerun_without_requests_or_duplicate_rows(self):
        self.assertEqual(self.runner().collect('2026-11-02')['state'],'complete')
        self.assertEqual(self.count(),2)
        self.assertEqual(len(self.calls),4)
        self.assertEqual(self.runner().collect('2026-11-02')['state'],'complete_reused')
        self.assertEqual((self.count(),len(self.calls)),(2,4))
        self.assertEqual(inc.completed_days(self.store.path),{'2026-11-02'})
        self.assertEqual(inc.integrity(self.store.path)['integrity_check'],['ok'])

    def test_weekend_today_future_do_not_call_api(self):
        for day,state in [('2026-11-01','nontrading_weekend'),('2026-11-04','deferred_current_or_future_day'),
                          ('2026-11-05','deferred_current_or_future_day')]:
            self.assertEqual(self.runner().collect(day)['state'],state)
        self.assertEqual(self.calls,[])

    def test_empty_retried_after_restart_and_not_declared_holiday(self):
        for s in ('stk_bydd_trd','ksq_bydd_trd'):self.overrides[s]={'raw_response':{'OutBlock_1':[]}}
        first=self.runner().collect('2026-11-02')
        self.assertEqual(first['state'],'empty_unclassified')
        self.assertFalse(first['details']['holiday_confirmed'])
        self.assertEqual(self.count(),0)
        self.overrides.clear()
        self.assertEqual(self.runner().collect('2026-11-02')['state'],'complete')
        self.assertEqual(len(self.calls),6)

    def test_one_market_empty_publishes_neither_market(self):
        self.overrides['ksq_bydd_trd']={'raw_response':{'OutBlock_1':[]}}
        self.assertEqual(self.runner().collect('2026-11-02')['state'],'partial_market_availability')
        self.assertEqual(self.count(),0)

    def test_failed_response_retries_on_next_run_preserving_prior_day(self):
        self.runner().collect('2026-11-02')
        self.overrides['stk_bydd_trd']={'http_status':500,'raw_response':None}
        self.assertEqual(self.runner().collect('2026-11-03')['state'],'request_failed')
        self.assertEqual(self.count(),2)
        self.overrides.clear()
        self.assertEqual(self.runner().collect('2026-11-03')['state'],'complete')
        self.assertEqual(self.count(),4)

    def test_wrong_basis_date_rejected(self):
        self.overrides['ksq_bydd_trd']=lambda r:r['raw_response']['OutBlock_1'][0].update(BAS_DD='20261030')
        self.assertEqual(self.runner().collect('2026-11-02')['state'],'failed_or_quarantined')
        self.assertEqual(self.count(),0)

    def test_incomplete_numbers_rejected(self):
        self.overrides['ksq_bydd_trd']=lambda r:r['raw_response']['OutBlock_1'][0].update(ACC_TRDVOL='-')
        result=self.runner().collect('2026-11-02')
        self.assertEqual(result['state'],'failed_or_quarantined')
        self.assertEqual(self.count(),0)

    def test_master_coverage_gate_rejects_truncated_daily(self):
        def extra(r):
            row=deepcopy(r['raw_response']['OutBlock_1'][0]);row.update(ISU_CD='KR7SYNTH0099',ISU_SRT_CD='000099')
            r['raw_response']['OutBlock_1'].append(row)
        self.overrides['stk_isu_base_info']=extra
        self.assertEqual(self.runner().collect('2026-11-02')['state'],'failed_or_quarantined')
        self.assertEqual(self.count(),0)

    def test_cross_market_master_duplicate_rejected(self):
        self.overrides['ksq_isu_base_info']=lambda r:r['raw_response']['OutBlock_1'][0].update(ISU_SRT_CD='000001')
        self.assertEqual(self.runner().collect('2026-11-02')['state'],'failed_or_quarantined')
        self.assertEqual(self.count(),0)

    def test_atomic_publish_rolls_back_both_markets(self):
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.executescript("CREATE TRIGGER fail_kosdaq BEFORE INSERT ON market_days WHEN NEW.market='KOSDAQ' "
                             "BEGIN SELECT RAISE(ABORT,'injected failure'); END;")
        self.assertEqual(self.runner().collect('2026-11-02')['state'],'failed_or_quarantined')
        self.assertEqual(self.count(),0)
        self.assertEqual(self.count('snapshots'),0)

    def test_missing_date_is_replanned_after_later_success(self):
        self.runner().collect('2026-11-03')
        with closing(sqlite3.connect(self.checkpoint)) as db, db:
            db.execute("INSERT INTO dates VALUES('2026-11-02','empty_unclassified','{}')")
        last,days=inc.plan(self.store.path,self.checkpoint,'2026-11-03')
        self.assertEqual(last,'2026-11-03');self.assertEqual(days,['2026-11-02'])

    def test_master_refresh_is_independent_of_daily_store(self):
        result=self.runner().masters('2026-11-02')
        self.assertEqual(set(result),set(inc.MARKETS));self.assertEqual(self.count(),0)
        self.assertEqual(len(self.calls),2)

    def test_adr_reuses_common_policy_ratio_of_sums_and_html(self):
        self.runner().collect('2026-11-02')
        with inc.master.read_db(self.store.path) as db:
            rows=[dict(r) for r in db.execute('select * from daily_latest')]
        days=[f'2026-10-{i:02d}' for i in range(1,21)]
        template=rows[0]
        from decimal import Decimal
        for market in ('KOSPI','KOSDAQ'):
            counts={d:dict(day=d,up=i+1,down=2,flat=0,complete=True) for i,d in enumerate(days)}
            result=list(adr.windowed(days,counts))[-1]
            self.assertEqual(Decimal(result['value']),Decimal(100)*210/40)
        groups,_=adr.aggregate(rows,['2026-11-02'],{('2026-11-02',m) for m in inc.MARKETS})
        self.assertEqual(groups[('2026-11-02','COMBINED','common')]['total'],2)
        from kiwoom_collector.krx_adr_chart import render
        self.assertIn('<svg',render(dict(report={},series=[])))

    def test_backup_preserves_seed_and_integrity(self):
        before=self.store.path.read_bytes();out=self.root/'copy.db'
        inc.backup_new(self.store.path,out)
        self.assertEqual(self.store.path.read_bytes(),before)
        self.assertEqual(inc.integrity(out)['integrity_check'],['ok'])

    def test_adr_revision_changes_only_when_daily_pointers_change(self):
        before=inc.revision(self.store.path)
        self.runner().collect('2026-11-02')
        after=inc.revision(self.store.path)
        self.assertNotEqual(before,after)
        self.runner().collect('2026-11-02')
        self.assertEqual(inc.revision(self.store.path),after)

    def test_concurrent_process_lock_is_rejected(self):
        root=self.root/'owned-lock'
        with inc.run_lock(root):
            with self.assertRaises((ValueError,BlockingIOError)):
                with inc.run_lock(root):self.fail('second updater acquired lock')
        with inc.run_lock(root):pass


class TransportTests(unittest.TestCase):
    def test_transient_failure_then_success_reuses_backfill_retry(self):
        with tempfile.TemporaryDirectory() as t,patch.object(backfill,'ROOT',Path(t).resolve()):
            receipts=[dict(service='stk_bydd_trd',basis_date='2026-11-02',source_kind='krx_live',http_status=s,
                 raw_response={'OutBlock_1':[]} if s==200 else None) for s in (500,200)]
            calls=[]
            def fetch(*args):calls.append(args);return receipts.pop(0)
            runner=backfill.Backfill(None,Path(t).resolve()/'run',fetch,sleep=lambda _:None)
            self.assertEqual(runner.receipt('2026-11-02','stk_bydd_trd')[0]['http_status'],200)
            self.assertEqual(len(calls),2)

    def test_private_key_path_without_key_disclosure(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)/'dev';private=Path(t)/'dev-private';root.mkdir();private.mkdir()
            path=private/'.env.krx.local';path.write_text('KRX_AUTH_KEY=synthetic-only-key')
            with patch.object(api,'PROJECT',root):
                key=api.credential(env_file=path)
                self.assertNotIn('synthetic-only-key',repr(key))
                elsewhere=Path(t)/'.env.krx.local';elsewhere.write_text('KRX_AUTH_KEY=synthetic-only-key')
                with self.assertRaises(api.KrxError):api.credential(env_file=elsewhere)


if __name__=='__main__':unittest.main()
