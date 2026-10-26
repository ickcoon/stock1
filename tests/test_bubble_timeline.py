"""Prefix isolation, stable geometry and real timeline/browser interactions."""
from contextlib import closing
from datetime import datetime
from copy import deepcopy
import math
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_sector_bubble as fixtures
from bubble_browser_helpers import browser_page
from test_workbench import browser_path
from kiwoom_collector.client import KST
from kiwoom_collector import bubble_timeline as timeline
from kiwoom_collector.radar import radar_report
from kiwoom_collector.summary import read_database


class BubbleTimelineTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.SectorBubbleTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f=self.fixture.fixture
        self.path=self.fixture.path
        self.output=self.fixture.output

    def data(self, environment='mock'):
        return timeline.report(self.path,'2026-10-20',environment)

    def test_default_latest_only_same_day_environment_query_and_operating(self):
        self.f.run_at('11:00');self.f.run_at('09:20');self.f.run_at('12:00',qry='4')
        self.f.run_at('13:00',mode=1);self.f.run_at('14:00',environment='real')
        self.f.fixture.run_at('2026-10-21T09:20:00+09:00',mode=0)
        data=self.data()
        self.assertEqual([f['data']['current']['collection']['collected_at'][11:16] for f in data['frames']],['09:20','11:00'])
        self.assertEqual(data['default_index'],1)
        self.assertEqual(len(self.data('real')['frames']),1)
        self.assertIn('id="time-1" aria-label="11:00:00 회차 선택" checked',timeline.render(data))

    def test_equal_instants_use_sequence_and_exact_prefix_not_timestamp_only(self):
        a=self.f.run_at('09:20');b=self.f.run_at('09:30')
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE collections SET collected_at='2026-10-20T00:20:00+00:00' WHERE id=?",(b,));db.commit()
        data=self.data()
        self.assertEqual([f['collection_id'] for f in data['frames']],[a,b])
        self.assertEqual([len(f['data']['snapshots']) for f in data['frames']],[1,2])
        self.assertIsNone(data['frames'][0]['data']['previous'])
        self.assertEqual(data['frames'][1]['data']['gap_seconds'],0)
        html=timeline.render(data)
        self.assertIn('같은 시각 1/2',html);self.assertIn('같은 시각 2/2',html)

    def test_prefix_counts_colors_members_and_first_comparison(self):
        self.fixture.two_runs()
        data=self.data();first,last=data['frames']
        a=next(g for g in first['model']['groups'] if g['name']=='반도체')
        b=next(g for g in last['model']['groups'] if g['name']=='반도체')
        self.assertEqual((a['count'],a['state'],a['delta20']),(3,'unknown',None))
        self.assertEqual((b['count'],b['state'],b['delta20']),(2,'decrease',-1))
        self.assertEqual((len(last['data']['entrants']),len(last['data']['retained']),len(last['data']['exits'])),(2,18,2))

    def test_persistence_has_no_future_observation_or_future_sector(self):
        self.f.fixture.tag('지속',1);self.f.fixture.tag('나중',21)
        self.f.run_at();self.f.run_at('09:30',codes=tuple(range(2,22)));self.f.run_at('09:40')
        data=self.data()
        self.assertEqual([len(f['data']['snapshots']) for f in data['frames']],[1,2,3])
        self.assertNotIn('나중',[g['name'] for g in data['frames'][0]['model']['groups']])
        group=next(g for g in data['frames'][-1]['model']['groups'] if g['name']=='지속')
        self.assertEqual((group['appearances'],group['observations'],group['streak']),(2,3,1))

    def test_new_disappeared_and_reentry_sectors(self):
        self.f.fixture.tag('로봇',1)
        self.f.run_at(codes=tuple(range(2,22)));self.f.run_at('09:30');self.f.run_at('09:40',codes=tuple(range(2,22)))
        data=self.data()
        middle,last=data['frames'][1:]
        self.assertTrue(next(g for g in middle['model']['groups'] if g['name']=='로봇')['new_sector'])
        self.assertEqual(last['model']['exits'][0]['count'],0)
        self.assertNotIn('로봇',[n['name'] for n in last['model']['desktop']['nodes']])

    def test_missing_and_failed_slots_not_frames_and_actual_interval(self):
        self.f.run_at('10:00');self.f.run_at('11:00',status='error');self.f.run_at('12:00')
        data=self.data();last=data['frames'][-1]['data']
        self.assertEqual(len(data['frames']),2)
        self.assertEqual(last['gap_seconds'],7200)
        self.assertTrue(any(s['state']=='missing' for s in last['intervening_slots']))
        self.assertIn('120분',timeline.frame_html(data['frames'][-1]))

    def test_multitag_and_unclassified_unique_count_in_every_frame(self):
        self.f.fixture.tag('A',1);self.f.fixture.tag('B',1)
        self.f.run_at();self.f.run_at('09:30')
        for frame in self.data()['frames']:
            self.assertEqual(frame['data']['current']['market']['unique_count'],20)
            self.assertEqual(sum(g['count'] for g in frame['model']['groups']),2)
            self.assertEqual(sum(not s['tags'] for s in frame['data']['current']['stocks']),19)
            self.assertNotIn('미분류',[n['name'] for n in frame['model']['desktop']['nodes']])

    def test_stable_centers_and_equal_areas_across_frames(self):
        self.f.fixture.tag('A',1,2);self.f.fixture.tag('B',3)
        self.f.run_at();self.f.run_at('09:30',codes=tuple(range(2,22)))
        frames=self.data()['frames']
        for key in ('desktop','mobile'):
            a={n['name']:n for n in frames[0]['model'][key]['nodes']}
            b={n['name']:n for n in frames[1]['model'][key]['nodes']}
            for name in a:
                self.assertEqual((a[name]['x'],a[name]['y']),(b[name]['x'],b[name]['y']))
            self.assertEqual(a['B']['r'],b['B']['r'])
            self.assertAlmostEqual(a['A']['r']**2/b['A']['r']**2,2)

    def test_frame_geometry_bounds_nonoverlap_and_input_unchanged(self):
        for i in range(1,10):self.f.fixture.tag('섹터'+str(i),i)
        self.f.run_at();self.f.run_at('09:30',codes=tuple(range(2,22)))
        data=self.data();before=deepcopy(data)
        timeline.render(data)
        self.assertEqual(data,before)
        for f in data['frames']:
            for key in ('desktop','mobile'):
                geometry=f['model'][key]
                for a in geometry['nodes']:
                    self.assertGreaterEqual(a['x']-a['r'],0)
                    self.assertLessEqual(a['y']+a['r'],geometry['height'])
                    for b in geometry['nodes']:
                        if a!=b:self.assertGreater(math.hypot(a['x']-b['x'],a['y']-b['y']),a['r']+b['r'])

    def test_quote_batches_not_carried_from_other_frame(self):
        first=self.f.run_at('10:00');self.f.scheduled(first)
        self.f.fixture.batch(first,mode=0,batch_id='first')
        second=self.f.run_at('11:00');self.f.scheduled(second)
        self.f.fixture.batch(second,mode=0,batch_id='failed',status='error',values={})
        data=self.data()
        self.assertEqual(data['frames'][0]['data']['current']['market']['turnover_count'],20)
        self.assertEqual(data['frames'][1]['data']['current']['market']['turnover_count'],0)
        self.assertEqual(data['frames'][1]['data']['current']['batch_id'],'failed')

    def test_empty_and_all_unclassified_remain_usable(self):
        empty=self.data();self.assertIsNone(empty['default_index'])
        self.assertIn('성공한 운영 관측이 없습니다',timeline.render(empty))
        self.f.run_at();data=self.data()
        self.assertIn('미분류 &lt;b&gt;20',timeline.render(data))

    def test_readonly_wal_no_api_and_export_protection(self):
        self.f.run_at();before=self.f.dump()
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')):
            info=timeline.export(self.path,'2026-10-20','mock',self.output)
        self.assertEqual(info['collections'],1);self.assertEqual(before,self.f.dump())
        for path in (self.output,self.path,self.output.with_suffix('.txt')):
            with self.assertRaises(ValueError):timeline.export(self.path,'2026-10-20','mock',path)

    def test_escaped_srcdoc_no_scripts_and_reduced_motion(self):
        self.f.fixture.tag('</iframe><script>alert(1)</script>',1);self.f.run_at()
        html=timeline.render(self.data())
        self.assertNotIn('<script',html)
        self.assertIn('&amp;lt;script&amp;gt;',html)
        self.assertIn('prefers-reduced-motion:reduce',html)
        self.assertIn('sandbox="allow-same-origin"',html)
        self.assertNotIn('allow-scripts',html)
        self.assertIn("default-src 'none'",html)

    def test_actual_cli_only_operating_query_one(self):
        self.f.run_at()
        env=os.environ.copy();env.update(PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1')
        args=[sys.executable,'-B','-m','kiwoom_collector.bubble_timeline','--db',str(self.path),'--date','2026-10-20','--output',str(self.output)]
        run=subprocess.run(args,env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(json.loads(run.stdout)['test_mode'],0)
        rejected=subprocess.run(args+['--test-mode','1'],env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(rejected.returncode,2)

    @unittest.skipUnless(browser_path(),'Chromium not installed')
    def test_browser_time_selection_replaces_all_visible_content_and_retains_controls(self):
        self.fixture.two_runs();data=self.data()
        with browser_page(timeline.render(data),self.output.parent) as (command,evaluate,click):
            visible="Array.from(document.querySelectorAll('iframe')).filter(f=>getComputedStyle(f).display!='none')"
            self.assertEqual(evaluate(visible+'.length'),1)
            self.assertEqual(evaluate(visible+'[0].id'),'frame-1')
            self.assertTrue(evaluate("document.querySelector('#frame-1').contentDocument.body.textContent.includes('성공 관측')"))
            click('label[for=time-0]')
            self.assertEqual(evaluate(visible+'[0].id'),'frame-0')
            self.assertTrue(evaluate("document.querySelector('#frame-0').contentDocument.querySelector('.brief').textContent.includes('비교 불가')"))
            self.assertEqual(evaluate("document.querySelector('#frame-0').contentDocument.querySelector('.detail-grid').textContent.includes('1/1회')"),True)
            click('label[for=time-1]',touch=True)
            self.assertEqual(evaluate(visible+'[0].id'),'frame-1')
            self.assertTrue(evaluate("document.querySelector('#frame-1').contentDocument.querySelector('.detail-grid').textContent.includes('2/2회')"))
            # Interact with the preserved Bubble v1 controls in the active srcdoc.
            point=evaluate("(()=>{const f=document.querySelector('#frame-1'),r=f.contentDocument.querySelector('label[for=mode-table]').getBoundingClientRect(),a=f.getBoundingClientRect();return{x:r.x+a.x+r.width/2,y:r.y+a.y+r.height/2}})()")
            command('Input.dispatchMouseEvent',dict(type='mousePressed',button='left',clickCount=1,**point))
            command('Input.dispatchMouseEvent',dict(type='mouseReleased',button='left',clickCount=1,**point))
            self.assertTrue(evaluate("document.querySelector('#frame-1').contentDocument.getElementById('mode-table').checked"))
            click('label[for=time-0]')
            self.assertTrue(evaluate("document.querySelector('#frame-0').contentDocument.getElementById('mode-map').checked"))

    @unittest.skipUnless(browser_path(),'Chromium not installed')
    def test_browser_mobile_motion_disable_and_keyboard_timeline(self):
        self.fixture.two_runs();html=timeline.render(self.data())
        with browser_page(html,self.output.parent,width=390) as (command,evaluate,click):
            self.assertLessEqual(evaluate('document.body.scrollWidth'),390)
            click('label[for=time-0]',touch=True)
            self.assertTrue(evaluate('document.getElementById("time-0").checked'))
            evaluate('document.getElementById("time-0").focus()')
            command('Input.dispatchKeyEvent',dict(type='keyDown',key='ArrowRight',code='ArrowRight',windowsVirtualKeyCode=39))
            command('Input.dispatchKeyEvent',dict(type='keyUp',key='ArrowRight',code='ArrowRight',windowsVirtualKeyCode=39))
            self.assertTrue(evaluate('document.getElementById("time-1").checked'))
            click('label.motion-toggle')
            self.assertEqual(evaluate('getComputedStyle(document.getElementById("frame-1")).animationName'),'none')
            command('Emulation.setEmulatedMedia',dict(features=[dict(name='prefers-reduced-motion',value='reduce')]))
            click('label.motion-toggle')
            self.assertEqual(evaluate('getComputedStyle(document.getElementById("frame-1")).animationName'),'none')


if __name__=='__main__':unittest.main()
