"""TOP5 prefix metrics, schedule gaps, unchanged legacy output and touch panels."""
from copy import deepcopy
from contextlib import closing
import json
import os
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_bubble_timeline as fixtures
from bubble_browser_helpers import browser_page
from test_workbench import browser_path
from kiwoom_collector import bubble_core as core
from kiwoom_collector import bubble_layout as layout


class BubbleCoreTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.BubbleTimelineTests('runTest');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f=self.fixture.f;self.path=self.fixture.path;self.output=self.fixture.output

    def run_at(self,time='09:20',codes=tuple(range(1,21)),scheduled=True,**kw):
        run=self.f.run_at(time,codes=codes,**kw)
        if scheduled:self.f.scheduled(run)
        return run

    def data(self):
        return core.report(self.path,'2026-10-20','mock')

    def metrics(self,data,index=-1):
        return data['frames'][index]['core']['metrics']

    def three(self):
        self.f.fixture.tag('반도체',1,5,6,7)
        self.run_at()
        self.run_at('09:30',codes=(6,1,2,3,4,5,*range(7,21)))
        self.run_at('09:40',codes=(*range(7,21),*range(1,7)))
        return self.data()

    def test_first_middle_last_no_future_best_or_counts(self):
        data=self.three()
        first=self.metrics(data,0)['stocks'];middle=self.metrics(data,1)['stocks'];last=self.metrics(data)['stocks']
        self.assertEqual((first['000006']['top5_appearances'],first['000006']['best_rank']),(0,6))
        self.assertEqual((middle['000006']['top5_appearances'],middle['000006']['best_rank']),(1,1))
        self.assertEqual((first['000001']['top5_state'],first['000001']['previous_rank']),('비교 불가',None))
        self.assertEqual((middle['000006']['top5_state'],middle['000001']['top5_state']),('신규 진입','유지'))
        self.assertEqual((middle['000001']['top5_streak'],last['000001']['top5_streak']),(2,0))
        self.assertEqual((last['000001']['rank'],last['000001']['previous_rank'],last['000001']['best_rank']),(15,2,1))
        self.assertEqual((last['000005']['top5_state'],last['000005']['top20_state']),('TOP5 밖','유지'))
        self.assertEqual((last['000006']['top5_state'],last['000006']['top20_state']),('이탈','유지'))

    def test_top5_exit_and_top20_exit_are_distinct(self):
        self.f.fixture.tag('A',1,2,6)
        self.run_at();self.run_at('09:30',codes=tuple(range(2,22)))
        metrics=self.metrics(self.data())['stocks']
        self.assertEqual((metrics['000001']['top5_state'],metrics['000001']['top20_state']),('이탈','이탈'))
        self.assertEqual((metrics['000006']['top5_state'],metrics['000006']['top20_state']),('신규 진입','유지'))

    def test_adjacent_schedule_streak_and_missing_slot_reset(self):
        self.run_at();self.run_at('09:30');self.run_at('09:50')
        data=self.data()
        self.assertEqual([self.metrics(data,i)['stocks']['000001']['top5_streak'] for i in range(3)],[1,2,1])
        self.assertEqual(self.metrics(data)['stocks']['000001']['top5_appearances'],3)
        self.assertEqual(self.metrics(data)['stocks']['000001']['top5_state'],'유지')
        self.assertIn('누락',self.metrics(data)['continuity'])

    def test_hourly_adjacent_slots_are_continuous(self):
        self.run_at('10:00');self.run_at('11:00');self.run_at('12:00')
        self.assertEqual(self.metrics(self.data())['stocks']['000001']['top5_streak'],3)

    def test_unknown_schedule_and_duplicate_slot_do_not_assert_continuity(self):
        self.run_at(scheduled=False);self.run_at('09:30');self.run_at('09:40')
        data=self.data()
        self.assertEqual([self.metrics(data,i)['stocks']['000001']['top5_streak'] for i in range(2)],[1,1])
        self.assertIn('미확인',self.metrics(data,1)['continuity'])
        raw=deepcopy(data['frames'][-1]['data'])
        raw['snapshots'][-1]['collection']['scheduled_at']='2026-10-20T09:30:00+09:00'
        self.assertEqual(core.stock_metrics(raw)['stocks']['000001']['top5_streak'],1)

    def test_delayed_and_failed_observations_do_not_bridge_scheduled_gaps(self):
        first=self.run_at();self.run_at('09:30',status='error');self.run_at('09:40')
        self.assertEqual(self.metrics(self.data())['stocks']['000001']['top5_streak'],1)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE collections SET collected_at='2026-10-20T09:35:00+09:00' WHERE id=?",(first,))
            db.commit()
        self.assertEqual(self.metrics(self.data())['stocks']['000001']['top5_streak'],1)

    def test_scope_filter_mock_real_query_test_failure_and_other_day(self):
        self.run_at();self.run_at('09:30',qry='4');self.run_at('09:40',mode=1)
        self.run_at('09:50',environment='real');self.run_at('10:00',status='error')
        data=self.data()
        self.assertEqual(len(data['frames']),1)
        first=data['frames'][0]['data']; polluted=deepcopy(first)
        future=deepcopy(first['snapshots'][0]);future['collection']['sequence']+=100
        polluted['snapshots'].append(future)
        for key,value in [('environment','real'),('qry_tp','4'),('test_mode',1),('status','error'),('collected_at','2026-10-21T09:20:00+09:00')]:
            other=deepcopy(first['snapshots'][0]);other['collection'][key]=value
            polluted['snapshots'].append(other)
        self.assertEqual(core.stock_metrics(polluted),core.stock_metrics(first))

    def test_duplicate_code_and_multitag_share_global_count(self):
        self.f.fixture.tag('A',1);self.f.fixture.tag('B',1)
        self.run_at();self.run_at('09:30')
        source=layout.report(self.path,'2026-10-20')
        for frame in source['frames']:
            for snapshot in frame['data']['snapshots']:
                duplicate=deepcopy(snapshot['stocks'][0]);duplicate['rank']=19
                snapshot['stocks'].append(duplicate)
        data=core.enhance(source);metrics=self.metrics(data)['stocks']
        self.assertEqual((metrics['000001']['top5_appearances'],metrics['000001']['top20_appearances']),(2,2))
        copies=[g['core'][0] for g in data['frames'][-1]['core']['sectors'].values()]
        self.assertEqual(len(copies),2);self.assertEqual(copies[0],copies[1])

    def test_incomplete_snapshot_does_not_invent_missing_exit_or_new(self):
        self.run_at();self.run_at('09:30')
        data=self.data(); raw=deepcopy(data['frames'][-1]['data'])
        raw['snapshots'][-1]['stocks']=raw['snapshots'][-1]['stocks'][1:4]
        metrics=core.stock_metrics(raw)
        self.assertTrue(metrics['incomplete'])
        self.assertEqual((metrics['stocks']['000001']['top5_state'],metrics['stocks']['000001']['top20_state']),('비교 불가','관측 미확인'))

    def test_support_contains_repeated_non_top5_without_removing_all_top20(self):
        self.f.fixture.tag('A',1,10,11);self.run_at();self.run_at('09:30')
        frame=self.data()['frames'][-1];sector=next(iter(frame['core']['sectors'].values()))
        self.assertEqual([s['code'] for s in sector['core']],['000001'])
        self.assertEqual([s['top20_appearances'] for s in sector['support']],[2,2])
        html=core.frame_html(frame)
        self.assertIn('보조 관찰 · 나머지 TOP20 2종목 · TOP20 반복 2',html)
        self.assertNotIn('<details class="core-list support" open',html)

    def test_no_current_quotes_backfilled_from_previous_batch(self):
        self.f.fixture.tag('A',1)
        first=self.run_at('10:00');self.f.fixture.batch(first,mode=0,batch_id='first')
        second=self.run_at('11:00');self.f.fixture.batch(second,mode=0,batch_id='failed',status='error',values={})
        data=self.data();a=self.metrics(data,0)['stocks']['000001'];b=self.metrics(data,1)['stocks']['000001']
        self.assertTrue(a['cash_verified']);self.assertFalse(b['cash_verified'])
        self.assertFalse(a['candidate_confirmed']);self.assertFalse(a['liquidity_verified'])
        self.assertIn(core.NOTICE,core.frame_html(data['frames'][-1]))

    def test_quotes_completed_after_selected_time_do_not_verify_panel(self):
        self.f.fixture.tag('A',1)
        first=self.run_at('10:00');self.f.fixture.batch(first,mode=0,at='2026-10-20T10:00:01+09:00')
        stock=self.metrics(self.data())['stocks']['000001']
        self.assertFalse(stock['rate_verified']);self.assertFalse(stock['cash_verified'])
        self.assertEqual(stock['cash'],'미확인');self.assertIn('선택 시각 이후',stock['cash_reason'])

    def test_delayed_adjacent_scheduled_connection_is_not_continuous(self):
        a=dict(collection=dict(scheduled_at='2026-10-20T09:20:00+09:00',collected_at='2026-10-20T09:35:00+09:00'))
        b=dict(collection=dict(scheduled_at='2026-10-20T09:30:00+09:00',collected_at='2026-10-20T09:40:00+09:00'))
        ok,reason=core.connection(a,b)
        self.assertFalse(ok);self.assertIn('지연',reason)

    def test_rate_and_cash_cutoffs_are_checked_independently(self):
        self.run_at('10:00');data=self.data();raw=deepcopy(data['frames'][0]['data'])
        stock=raw['snapshots'][0]['stocks'][0]
        stock['valid_quote']=dict(request_finished_at='2026-10-20T10:00:00+09:00',api_at='2026-10-20T10:00:00+09:00',change_percent='2',direction='up')
        stock['cash_quote']=dict(request_finished_at='2026-10-20T10:00:01+09:00',api_at='2026-10-20T10:00:00+09:00')
        stock['cash']='100';stock['cash_reason']=None
        result=core.stock_metrics(raw)['stocks']['000001']
        self.assertTrue(result['rate_verified']);self.assertFalse(result['cash_verified'])
        self.assertIn('선택 시각 이후',result['cash_reason'])

    def test_original_data_renderers_and_model_unchanged(self):
        self.f.fixture.tag('A',1);self.run_at()
        source=layout.report(self.path,'2026-10-20');before=deepcopy(source);html=layout.render(source)
        result=core.enhance(source);core.render(result)
        self.assertEqual(source,before);self.assertEqual(layout.render(source),html)
        self.assertIs(result['frames'][0]['data'],source['frames'][0]['data'])
        self.assertEqual(result['frames'][0]['model'],source['frames'][0]['model'])

    def test_empty_and_unclassified_no_fake_core(self):
        self.assertIn('성공한 운영 관측이 없습니다',core.render(self.data()))
        self.run_at();data=self.data();self.assertFalse(data['frames'][0]['core']['sectors'])
        self.assertIn('미분류',core.render(data))

    def test_escaping_and_preserved_csp_details_and_tables(self):
        self.f.fixture.tag('<script>sector</script>',1);self.run_at()
        html=core.render(self.data())
        self.assertNotIn('<script',html);self.assertIn('&amp;lt;script&amp;gt;',html)
        for text in ('기존 섹터 상세·검증 근거','기존 v1 진단','prefers-reduced-motion','allow-same-origin'):
            self.assertIn(text,html)

    def test_readonly_export_and_cli_and_existing_file_protection(self):
        self.f.fixture.tag('A',1);self.run_at();before=self.f.dump()
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')):
            result=core.export(self.path,'2026-10-20','mock',self.output)
        self.assertEqual(self.f.dump(),before);self.assertEqual(result['collections'],1)
        for path in (self.output,self.path,self.output.with_suffix('.csv')):
            with self.assertRaises(ValueError):core.export(self.path,'2026-10-20','mock',path)
        target=self.output.with_name('cli-core.html')
        args=[sys.executable,'-B','-m','kiwoom_collector.bubble_core','--db',str(self.path),'--date','2026-10-20','--output',str(target)]
        env=dict(os.environ,PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1')
        run=subprocess.run(args,env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(run.returncode,0,run.stderr);self.assertEqual(json.loads(run.stdout)['qry_tp'],'1')

    @unittest.skipUnless(browser_path(),'Chromium not installed')
    def test_browser_first_middle_last_and_sector_selection_pc_mobile(self):
        data=self.three();html=core.render(data)
        for width,height in ((1360,900),(390,844)):
            with browser_page(html,self.output.parent,width=width,height=height) as (command,evaluate,click):
                for index in (0,1,2):
                    click('label[for=time-'+str(index)+']',touch=width<540)
                    doc=f"document.getElementById('frame-{index}').contentDocument"
                    self.assertEqual(evaluate("Array.from(document.querySelectorAll('iframe')).filter(f=>getComputedStyle(f).display!='none').length"),1)
                    self.assertIn('선택 시각까지 '+str(index+1)+'회',evaluate(doc+'.querySelector(".sector-panel").textContent'))
                    self.assertLessEqual(evaluate(doc+'.body.scrollWidth'),evaluate(f"document.getElementById('frame-{index}').clientWidth"))
                    self.assertLessEqual(evaluate('document.body.scrollWidth'),width)
                self.assertIn('TOP5 이탈',evaluate(doc+'.querySelector(".sector-panel").textContent'))


if __name__=='__main__':unittest.main()
