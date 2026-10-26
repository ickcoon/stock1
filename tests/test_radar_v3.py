"""Offline v3 scope, persistence, changes, rendering and preservation checks."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_radar_v2 as fixtures
from kiwoom_collector.client import KST
from kiwoom_collector.radar import radar_report
from kiwoom_collector.radar_v3 import analyze, export, render, report, slot_states
from kiwoom_collector.scheduled_quotes import migrate_scheduled_quotes


class RadarV3Tests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RadarV2Tests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.path = self.fixture.path
        self.output = self.fixture.output
        # Prepare only the isolated test fixture for operational-mode quote batches.
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory=sqlite3.Row
            migrate_scheduled_quotes(db)
            db.executemany('INSERT INTO stocks VALUES(?,?,?)',
                           [(f'{i:06}',f'종목 {i}','2026-10-20T09:00:00+09:00') for i in range(1,23)])
            db.commit()

    def run_at(self, time='09:20', codes=tuple(range(1,21)), **kwargs):
        return self.fixture.run_at('2026-10-20T'+time+':00+09:00', codes=codes, mode=kwargs.pop('mode',0), **kwargs)

    def data(self, **kwargs):
        return report(self.path,'2026-10-20',**kwargs)

    def dump(self):
        with closing(sqlite3.connect(self.path)) as db:
            return list(db.iterdump())

    def scheduled(self, run):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE collections SET scheduled_at=collected_at WHERE id=?',(run,))
            db.commit()

    def visible(self, data):
        parser = fixtures.VisibleText()
        parser.feed(render(data))
        return ' '.join(parser.text)

    def test_default_scope_ignores_later_cumulative_query_modes_and_other_environment(self):
        self.run_at()
        self.run_at('09:30')
        for i,qry in enumerate(('2','3','4','5')):
            self.run_at(f'1{i}:00',qry=qry)
        self.run_at('15:00',mode=1)
        self.run_at('14:00',environment='real')
        data=self.data()
        self.assertEqual(len(data['snapshots']),2)
        self.assertEqual(data['current']['collection']['collected_at'],'2026-10-20T09:30:00+09:00')
        self.assertEqual(len(self.data(test_mode=1)['snapshots']),1)
        self.assertEqual(len(self.data(environment='real')['snapshots']),1)
        self.assertTrue(all(r['qry_tp']=='1' for r in data['legacy']['runs']))

    def test_top5_changes_without_top20_change_and_member_replacement(self):
        self.fixture.tag('반도체',1,2)
        self.run_at(codes=(1,*range(3,22)))
        self.run_at('09:30',codes=(*range(3,8),2,*range(8,22)))
        data=self.data(); g=data['groups'][0]
        self.assertEqual((g['delta20'],g['delta5']),(0,-1))
        self.assertEqual((g['new_codes'],g['exited_codes'],g['retained_codes']),(['000002'],['000001'],[]))
        self.assertEqual((len(data['entrants']),len(data['retained']),len(data['exits'])),(1,19,1))

    def test_disappeared_sector_remains_in_decreases(self):
        self.fixture.tag('로봇',1)
        self.run_at(); self.run_at('09:30',codes=tuple(range(2,22)))
        data=self.data(); g=data['decreases'][0]
        self.assertEqual((g['name'],g['count'],g['delta20'],g['streak']),('로봇',0,-1,0))
        self.assertIn('로봇',self.visible(data))

    def test_increase_and_decrease_are_independent_and_no_weighted_score(self):
        self.fixture.tag('증가',21); self.fixture.tag('감소',1)
        self.run_at(); self.run_at('09:30',codes=tuple(range(2,22)))
        data=self.data()
        self.assertEqual([g['name'] for g in data['increases']],['증가'])
        self.assertEqual([g['name'] for g in data['decreases']],['감소'])
        self.assertNotIn('score',data)

    def test_ten_run_persistence_top5_and_reentry(self):
        self.fixture.tag('로봇',1)
        for i,time in enumerate(('09:20','09:30','09:40','09:50','10:00','11:00','12:00','13:00','14:00','15:00')):
            self.run_at(time,codes=tuple(range(1,21)) if i!=8 else tuple(range(2,22)))
        data=self.data(); g=data['groups'][0]
        self.assertEqual((g['appearances'],g['observations'],g['streak'],g['top5_appearances']),(9,10,1,9))
        stock=next(s for s in data['repeated_stocks'] if s['code']=='000001')
        self.assertEqual((stock['appearances'],stock['streak']),(9,1))

    def test_gap_uses_actual_time_and_failed_slot_is_not_zero_observation(self):
        self.fixture.tag('로봇',1)
        self.run_at('10:00'); self.run_at('11:00',status='error'); self.run_at('12:00')
        data=self.data()
        self.assertEqual(data['gap_seconds'],7200)
        self.assertEqual(len(data['snapshots']),2)
        self.assertEqual(data['groups'][0]['history'],[1,1])
        self.assertIn('120분',self.visible(data))
        # Fixture manual observations do not pretend to be scheduled attempts.
        self.assertIn('missing',[s['state'] for s in data['intervening_slots']])

    def test_slot_future_error_running_duplicates_and_cumulative_exclusion(self):
        at=datetime(2026,10,20,11,30,tzinfo=KST)
        def row(time,state):
            return {'scheduled_at':f'2026-10-20T{time}:00+09:00','status':state}
        states=slot_states('2026-10-20',[row('09:20','error'),row('09:20','success'),row('11:00','running')],at)
        self.assertEqual(states[0]['attempts'],2)
        self.assertEqual(states[0]['state'],'success')
        self.assertEqual(states[5]['state'],'running')
        self.assertEqual(states[6]['state'],'future')
        self.run_at('09:20',qry='4')
        self.assertTrue(all(s['state']=='missing' for s in self.data()['slots']))

    def test_first_and_empty_reports_do_not_invent_deltas_or_entries(self):
        empty=self.data()
        self.assertIsNone(empty['current']); self.assertIn('성공한 순위 수집이 없습니다',render(empty))
        self.fixture.tag('로봇',1); self.run_at()
        data=self.data()
        self.assertIsNone(data['groups'][0]['delta20'])
        self.assertIsNone(data['entrants'])
        self.assertIsNone(data['retained'])

    def test_multisector_dedup_and_unclassified_are_not_sector_movers(self):
        self.fixture.tag('반도체',1); self.fixture.tag('소부장',1)
        self.run_at(); self.run_at('09:30')
        data=self.data()
        self.assertEqual(data['current']['market']['unique_count'],20)
        self.assertEqual(len(data['overlap']),1)
        self.assertEqual([g['count'] for g in data['groups']],[1,1])
        self.assertNotIn('미분류',[g['name'] for g in data['persistent']])
        self.assertIn('미분류 19종목',self.visible(data))

    def test_incomplete_sample_qualifies_changes(self):
        self.fixture.tag('로봇',1); self.run_at(); self.run_at('09:30',codes=(1,2))
        data=self.data()
        self.assertFalse(data['complete']); self.assertFalse(data['comparison_complete'])
        self.assertIn('불완전 TOP20 표본',self.visible(data))

    def test_future_observations_and_other_day_excluded(self):
        self.run_at(); self.run_at('15:00')
        self.fixture.run_at('2026-10-21T09:20:00+09:00',mode=0)
        legacy=radar_report(self.path,'2026-10-20')
        data=analyze(legacy,as_of=datetime(2026,10,20,12,tzinfo=KST))
        self.assertEqual(len(data['snapshots']),1)

    def test_alphanumeric_codes_remain_in_global_entries(self):
        # Update fixture rows as source observations; no importer or code conversion.
        self.run_at(); second=self.run_at('09:30',codes=tuple(range(2,22)))
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE ranking_records SET stk_cd='0035S0',stk_nm='문자종목' WHERE collection_id=? AND rank=20",(second,)); db.commit()
        data=self.data()
        self.assertEqual(data['current']['market']['unique_count'],20)
        self.assertEqual(data['entrants'][0]['stk_cd'],'0035S0')

    def test_cash_and_rate_policy_reuses_v2_and_never_confirms_mock(self):
        self.fixture.tag('로봇',1)
        run=self.run_at('10:00')
        self.scheduled(run)
        self.fixture.batch(run,mode=0,unit=False)
        data=self.data()
        self.assertEqual(data['current']['market']['turnover_count'],0)
        self.assertEqual(sum(bool(s['valid_quote']) for s in data['current']['stocks']),20)
        self.assertIn('모의 등락률·금액은 실전 검증을 증명하지 않습니다',self.visible(data))
        self.assertIn('거래대금 미검증',self.visible(data))

    def test_latest_failed_batch_does_not_backfill_old_verified_cash(self):
        run=self.run_at('10:00')
        self.scheduled(run)
        self.fixture.batch(run,mode=0,batch_id='old')
        self.fixture.batch(run,mode=0,batch_id='new',at='2026-10-20T10:00:10+09:00',status='error',values={})
        data=self.data()
        self.assertEqual(data['current']['batch_id'],'new')
        self.assertEqual(data['current']['market']['turnover_count'],0)

    def test_hidden_diagnostics_escaping_and_csp(self):
        self.fixture.tag('<img src=x onerror=alert(1)>',1); self.run_at()
        data=self.data(); html=render(data); visible=self.visible(data)
        self.assertNotIn(data['current']['collection']['id'],visible)
        self.assertNotIn('API 원본',visible)
        self.assertIn('&lt;img',html); self.assertNotIn('<img src=x',html)
        self.assertIn("default-src 'none'",html)
        self.assertNotIn('<script',html)

    def test_readonly_export_preserves_dump_and_refuses_existing_paths(self):
        self.run_at(); before=self.dump()
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')), patch('kiwoom_collector.storage.Store',side_effect=AssertionError('Store forbidden')):
            export(self.path,'2026-10-20','mock',self.output)
        self.assertEqual(before,self.dump())
        self.assertTrue(self.output.is_file())
        for path in (self.output,self.path,self.output.with_suffix('.txt')):
            with self.assertRaises(ValueError):
                export(self.path,'2026-10-20','mock',path)

    def test_wal_commits_are_included_and_writer_connection_not_closed(self):
        self.run_at()
        with closing(sqlite3.connect(self.path)) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute("UPDATE ranking_records SET stk_nm='WAL 관측' WHERE rank=1"); writer.commit()
            self.assertEqual(self.data()['current']['stocks'][0]['stk_nm'],'WAL 관측')
            self.assertEqual(writer.execute('SELECT 1').fetchone()[0],1)

    def test_actual_cli_with_network_guard_and_invalid_arguments(self):
        self.run_at()
        folder=self.output.parent
        guard=folder/'guard'; guard.mkdir()
        (guard/'sitecustomize.py').write_text("import socket\ndef no(*a,**kw): raise RuntimeError('network forbidden')\nsocket.socket.connect=no\nsocket.create_connection=no\n",encoding='utf-8')
        env=os.environ.copy(); env.update(PYTHONPATH=os.pathsep.join((str(guard),str(Path.cwd()))),PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1')
        args=[sys.executable,'-B','-m','kiwoom_collector.radar_v3','--db',str(self.path),'--date','2026-10-20','--output',str(self.output)]
        result=subprocess.run(args,env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('"qry_tp": "1"',result.stdout)
        result=subprocess.run(args,env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,1)


if __name__ == '__main__':
    unittest.main()
