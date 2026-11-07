"""Receipt-context and reference failure tests, entirely in development fixtures."""
from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from kiwoom_collector import c15_runtime,c15_references
from kiwoom_collector.am_time_policy import instant

ROOT=Path(__file__).resolve().parents[1]


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'diagnostics');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.path=self.root/'ranking.db'
        self.at=instant('2026-11-05T09:40:00+09:00')
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript('CREATE TABLE collections(id,collected_at,scheduled_at,environment,api_id,status,record_count,test_mode); CREATE TABLE ranking_records(collection_id,rank,stk_cd,raw_json);')
            db.execute('INSERT INTO collections VALUES(?,?,?,?,?,?,?,?)',('id',self.at.isoformat(),self.at.isoformat(),'real','ka00198','success',20,0))
            db.executemany('INSERT INTO ranking_records VALUES(?,?,?,?)',[('id',i,str(i).zfill(6),json.dumps(dict(dt='20261105'))) for i in range(1,21)])
            db.commit()

    def check(self,**kwargs):return c15_runtime.context(self.path,'id',self.at,kwargs.get('settings',{}),now=kwargs.get('now',self.at+timedelta(seconds=30)))
    def change(self,sql):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(sql);db.commit()
    def test_normal_receipt_not_price_proof(self):
        r=self.check();self.assertTrue(r['allowed']);self.assertFalse(r['recommendation_allowed']);self.assertFalse(r['market_asof_verified'])
    def test_replay_other_day(self):self.assertFalse(self.check(now=self.at+timedelta(days=1))['allowed'])
    def test_expired(self):self.assertFalse(self.check(now=self.at+timedelta(seconds=121))['allowed'])
    def test_future(self):self.assertFalse(self.check(now=self.at-timedelta(seconds=1))['allowed'])
    def test_holiday(self):
        p=self.root/'holidays.txt';p.write_text('2026-11-05',encoding='utf8')
        self.assertEqual(self.check(settings=dict(holidays_file=str(p)))['reason'],'market_closed_calendar')
    def test_bad_calendar(self):self.assertFalse(self.check(settings=dict(holidays_file=str(self.root/'missing')))['allowed'])
    def test_weekend(self):
        at=instant('2026-11-07T09:40:00+09:00')
        self.assertEqual(c15_runtime.context(self.path,'id',at,{},now=at)['reason'],'market_closed_calendar')
    def test_missing_or_incomplete(self):
        self.change('DELETE FROM ranking_records WHERE rank=20');self.assertFalse(self.check()['allowed'])
    def test_test_collection(self):
        self.change('UPDATE collections SET test_mode=1');self.assertFalse(self.check()['allowed'])
    def test_failed_collection(self):
        self.change("UPDATE collections SET status='error'");self.assertFalse(self.check()['allowed'])
    def test_past_response_date(self):
        self.change('UPDATE ranking_records SET raw_json=\'{"dt":"20261104"}\'');self.assertFalse(self.check()['allowed'])
    def test_future_response_date(self):
        self.change('UPDATE ranking_records SET raw_json=\'{"dt":"20261106"}\'');self.assertFalse(self.check()['allowed'])
    def test_late_collection_start(self):
        self.change("UPDATE collections SET collected_at='2026-11-05T09:40:06+09:00'");self.assertFalse(self.check()['allowed'])
    def test_readonly_source_unchanged(self):
        before=self.path.read_bytes();self.check();self.assertEqual(before,self.path.read_bytes())
    def test_missing_required_reference(self):
        p=self.root/'paths.json';p.write_text(json.dumps(dict(theme_master_db=str(self.root/'absent'),analysis_daily_db=str(self.root/'cap'),analysis_adr_db=str(self.root/'adr'))),encoding='utf8')
        r=c15_references.inspect(dict(runtime_root=str(self.root),analysis_paths=str(p)),day='2026-11-05',slot='09:40')
        self.assertFalse(r['ready']);self.assertEqual({s['role'] for s in r['sources'] if s['required'] and s['status']=='missing'},{'0198','theme'})
        self.assertFalse((self.root/'absent').exists())
