"""Area, state, safe v3 reuse and real offline browser interaction tests."""
from contextlib import closing
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_radar_v3 as fixtures
from test_workbench import browser_path
from bubble_browser_helpers import browser_page
from kiwoom_collector import sector_bubble as bubble
from kiwoom_collector import radar_v3


class SectorBubbleTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.RadarV3Tests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.path=self.fixture.path
        self.output=self.fixture.output

    def two_runs(self):
        f=self.fixture
        f.fixture.tag('반도체',1,2,3)
        f.fixture.tag('로봇',4,5)
        f.fixture.tag('방산',21)
        f.run_at()
        f.run_at('09:30',codes=(1,2,4,*range(6,23)))
        return f.data()

    def test_circle_area_ratio_and_equal_values(self):
        geometry=bubble.layout([dict(name=str(i),count=i) for i in range(1,9)])
        nodes=geometry['nodes']
        for a in nodes:
            for b in nodes:
                self.assertAlmostEqual(a['r']**2/b['r']**2,a['count']/b['count'])
        same=bubble.layout([dict(name='A',count=3),dict(name='B',count=3)])['nodes']
        self.assertEqual(same[0]['r'],same[1]['r'])

    def test_radius_bounds_and_nonoverlap_for_extreme_values(self):
        for mobile in (False,True):
            geo=bubble.layout([dict(name=str(i),count=20 if i%2 else 1) for i in range(8)],mobile=mobile)
            for a in geo['nodes']:
                self.assertGreaterEqual(a['r'],22)
                self.assertLessEqual(a['r'],102)
                self.assertGreaterEqual(a['x']-a['r'],0)
                self.assertLessEqual(a['x']+a['r'],geo['width'])
                for b in geo['nodes']:
                    if a!=b: self.assertGreater(math.hypot(a['x']-b['x'],a['y']-b['y']),a['r']+b['r'])

    def test_cell_positions_stable_when_counts_and_order_change(self):
        groups=[dict(name='반도체',count=3),dict(name='로봇',count=2),dict(name='바이오',count=1)]
        first=bubble.layout(groups)
        groups=list(reversed(groups));groups[0]['count']=4
        second=bubble.layout(groups)
        self.assertEqual({n['name']:(n['x'],n['y']) for n in first['nodes']},
                         {n['name']:(n['x'],n['y']) for n in second['nodes']})

    def test_all_change_states_and_partial_sample_comparison_hold(self):
        for delta,state in [(2,'increase'),(-1,'decrease'),(0,'steady'),(None,'unknown')]:
            self.assertEqual(bubble.change_state(dict(delta20=delta),True),state)
            self.assertEqual(bubble.change_state(dict(delta20=delta),False),'unknown')

    def test_new_and_exited_sectors_have_no_fake_zero_circle(self):
        data=self.two_runs()
        self.fixture.fixture.tag('이탈전용',3)
        data=self.fixture.data()
        m=bubble.model(data)
        self.assertTrue(next(g for g in m['groups'] if g['name']=='방산')['new_sector'])
        self.assertIn('이탈전용',[g['name'] for g in m['exits']])
        self.assertNotIn('이탈전용',[n['name'] for n in m['desktop']['nodes']])
        self.assertIn('현재 이탈 · 원 없음',bubble.render(data))

    def test_first_run_never_invents_delta_new_or_retained(self):
        self.fixture.fixture.tag('로봇',1);self.fixture.run_at()
        data=self.fixture.data();g=bubble.model(data)['groups'][0]
        self.assertEqual(g['state'],'unknown')
        self.assertFalse(g['new_sector'])
        self.assertTrue(all(s['status']=='비교 불가' for s in g['members']))
        self.assertIsNone(g['delta20'])

    def test_detail_current_previous_rank_members_and_quote_scope(self):
        data=self.two_runs();m=bubble.model(data)
        semi=next(g for g in m['groups'] if g['name']=='반도체')
        member={s['code']:s for s in semi['members']}
        self.assertEqual((member['000001']['rank'],member['000001']['previous_rank'],member['000001']['status']),(1,1,'유지'))
        self.assertEqual((member['000003']['rank'],member['000003']['previous_rank'],member['000003']['status']),(None,3,'이탈'))
        self.assertEqual(member['000003']['rate_scope'],'직전 관측')

    def test_unclassified_multisector_and_preserved_v3_payload(self):
        data=self.two_runs();self.fixture.fixture.tag('추가태그',1);data=self.fixture.data()
        m=bubble.model(data)
        self.assertNotIn('미분류',[g['name'] for g in m['groups']])
        self.assertEqual(data['current']['market']['unique_count'],20)
        before=deepcopy(data);html=bubble.render(data)
        self.assertEqual(data,before)
        self.assertIn('섹터 합계는 전체 고유 수를 초과할 수 있습니다',html)
        legacy=radar_v3.render(data).split('<main class="v3">',1)[1].rsplit('</main>',1)[0]
        self.assertIn(legacy,html)

    def test_display_limit_overflow_is_selectable_and_not_dropped(self):
        for i in range(1,14):self.fixture.fixture.tag('섹터'+str(i),i)
        self.fixture.run_at();data=self.fixture.data();m=bubble.model(data)
        self.assertEqual(len(m['selected']),8);self.assertEqual(len(m['remainder']),5)
        html=bubble.render(data)
        for g in m['remainder']:
            self.assertIn('id="detail-'+g['id']+'"',html)
            self.assertIn('for="pick-'+g['id']+'"',html)

    def test_empty_and_all_unclassified_reports_remain_usable(self):
        html=bubble.render(self.fixture.data())
        self.assertIn('선택 흐름에 성공한 순위 수집이 없습니다',html)
        self.fixture.run_at();html=bubble.render(self.fixture.data())
        self.assertIn('미분류 <b>20</b>',html)
        self.assertNotIn('class="bubble-node',html)

    def test_safe_labels_no_scripts_requests_or_csp_weakening(self):
        self.fixture.fixture.tag('</style><script>alert(1)</script>',1)
        self.fixture.run_at();html=bubble.render(self.fixture.data())
        self.assertNotIn('<script',html)
        self.assertIn('&lt;script&gt;',html)
        self.assertIn("default-src 'none'",html)
        self.assertNotIn('unsafe-eval',html)
        self.assertNotIn('onclick=',html)

    def test_export_readonly_v3_policy_query_isolation_and_no_overwrite(self):
        data=self.two_runs()
        self.fixture.run_at('15:00',qry='4')
        before=self.fixture.dump()
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')):
            info=bubble.export(self.path,'2026-10-20','mock',self.output)
        self.assertEqual(info['collections'],2)
        self.assertEqual(before,self.fixture.dump())
        with self.assertRaises(ValueError):bubble.export(self.path,'2026-10-20','mock',self.output)
        with self.assertRaises(ValueError):bubble.export(self.path,'2026-10-20','mock',self.path)

    def test_real_cli_exports_without_network_and_rejects_other_query_type(self):
        self.two_runs()
        guard=self.output.parent/'guard';guard.mkdir()
        (guard/'sitecustomize.py').write_text("import socket\ndef no(*a,**kw):raise RuntimeError('EXTERNAL_NETWORK_FORBIDDEN')\nsocket.socket.connect=no\n",encoding='utf-8')
        env=os.environ.copy()
        env.update(PYTHONPATH=os.pathsep.join((str(guard),str(Path.cwd()))),PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1')
        args=[sys.executable,'-B','-m','kiwoom_collector.sector_bubble','--db',str(self.path),'--date','2026-10-20','--output',str(self.output)]
        before=self.fixture.dump()
        completed=subprocess.run(args,env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(completed.returncode,0,completed.stderr)
        self.assertEqual(json.loads(completed.stdout)['collections'],2)
        self.assertEqual(before,self.fixture.dump())
        rejected=subprocess.run(args+['--qry-tp','4'],env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(rejected.returncode,2)

    @unittest.skipUnless(browser_path(),'Chromium not installed')
    def test_browser_click_touch_keyboard_and_view_switches(self):
        data=self.two_runs();m=bubble.model(data);html=bubble.render(data)
        first,second=m['selected'][:2]
        with browser_page(html,self.output.parent) as (command,evaluate,click):
            self.assertEqual(evaluate("document.querySelectorAll('.sector-panel').length"),len(m['groups']))
            click('.desktop-map .node-'+second['id']+' label')
            self.assertTrue(evaluate(f"document.querySelector('#pick-{second['id']}').checked"))
            self.assertEqual(evaluate(f"getComputedStyle(document.querySelector('#detail-{second['id']}')).display"),'block')
            click('.desktop-map .node-'+first['id']+' label',touch=True)
            self.assertTrue(evaluate(f"document.querySelector('#pick-{first['id']}').checked"))
            # Native radio arrow navigation works without scripted handlers.
            evaluate(f"document.querySelector('#pick-{first['id']}').focus()")
            command('Input.dispatchKeyEvent',dict(type='keyDown',key='ArrowRight',code='ArrowRight',windowsVirtualKeyCode=39))
            command('Input.dispatchKeyEvent',dict(type='keyUp',key='ArrowRight',code='ArrowRight',windowsVirtualKeyCode=39))
            self.assertFalse(evaluate(f"document.querySelector('#pick-{first['id']}').checked"))
            click('label[for=mode-table]')
            self.assertEqual(evaluate("getComputedStyle(document.querySelector('.legacy-panel')).display"),'block')
            self.assertEqual(evaluate("getComputedStyle(document.querySelector('.bubble-layout')).display"),'none')
            click('label[for=mode-both]')
            self.assertNotEqual(evaluate("getComputedStyle(document.querySelector('.bubble-layout')).display"),'none')
            self.assertEqual(evaluate("performance.getEntriesByType('resource').length"),0)

    @unittest.skipUnless(browser_path(),'Chromium not installed')
    def test_mobile_and_desktop_labels_and_circles_fit(self):
        for i in range(1,9):self.fixture.fixture.tag('아주긴섹터이름'+str(i),i)
        self.fixture.run_at();html=bubble.render(self.fixture.data())
        for width in (1360,390):
            with browser_page(html,self.output.parent,width=width) as (command,evaluate,click):
                self.assertLessEqual(evaluate('document.body.scrollWidth'),width)
                selector='.mobile-map' if width<540 else '.desktop-map'
                self.assertTrue(evaluate(f"Array.from(document.querySelectorAll('{selector} .bubble-label')).every(e=>e.scrollHeight<=e.clientHeight && e.scrollWidth<=e.clientWidth)"))
                first=evaluate(f"document.querySelector('{selector} .bubble-label').getAttribute('for')")
                click(selector+' .bubble-label',touch=True)
                self.assertTrue(evaluate(f"document.getElementById('{first}').checked"))


if __name__=='__main__':unittest.main()
