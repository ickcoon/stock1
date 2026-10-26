"""Display-only regression, density, all-sector access and real viewport checks."""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_bubble_timeline as fixtures
from bubble_browser_helpers import browser_page
from test_workbench import browser_path
from kiwoom_collector import bubble_layout as layout
from kiwoom_collector import bubble_timeline as timeline


class BubbleLayoutTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.BubbleTimelineTests('runTest')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.f=self.fixture.f;self.path=self.fixture.path;self.output=self.fixture.output

    def source(self,count=8,extreme=False):
        for i in range(count):
            codes=tuple(range(1,21)) if extreme and i==0 else (i+1,)
            self.f.fixture.tag('긴이름검증섹터'+str(i+1),*codes)
        self.f.run_at();self.f.run_at('09:30')
        return self.fixture.data()

    def test_optimization_preserves_every_analysis_value_and_source(self):
        source=self.source();before=deepcopy(source)
        optimized=layout.optimize(source)
        layout.render(optimized)
        self.assertEqual(source,before)
        for a,b in zip(source['frames'],optimized['frames']):
            self.assertIs(a['data'],b['data'])
            for key in ('groups','selected','remainder','exits','historical'):
                self.assertEqual(a['model'][key],b['model'][key])

    def test_one_four_eight_fifteen_twenty_sectors_and_all_details(self):
        source=self.source(20)
        for n in (1,4,8,15,20):
            with self.subTest(sectors=n):
                legacy=deepcopy(source['latest']['legacy'])
                allowed={g['name'] for g in source['frames'][-1]['model']['groups'][:n]}
                for run in legacy['runs']:
                    for stock in run['stocks']:
                        stock['tags']=[t for t in stock['tags'] if t['sector_name'] in allowed]
                cut=timeline.build(legacy)
                optimized=layout.optimize(cut)
                for f in optimized['frames']:
                    self.assertEqual(len(f['model']['desktop']['nodes']),min(n,3))
                    self.assertEqual(len(f['model']['mobile']['nodes']),min(n,3))
                    html=layout.frame_html(f)
                    for g in f['model']['groups']:
                        self.assertIn('id="detail-'+g['id']+'"',html)
                        self.assertIn('for="pick-'+g['id']+'"',html)

    def test_area_ratio_equal_values_extreme_counts_and_responsive_bounds(self):
        optimized=layout.optimize(self.source(8,extreme=True))
        for frame in optimized['frames']:
            for key in ('desktop','mobile'):
                geo=frame['model'][key]
                for a in geo['nodes']:
                    self.assertGreaterEqual(a['x']-a['hit_r'],0)
                    self.assertGreaterEqual(a['y']-a['hit_r'],0)
                    self.assertLessEqual(a['x']+a['hit_r'],geo['width'])
                    self.assertLessEqual(a['y']+a['hit_r'],geo['height'])
                    self.assertGreaterEqual(a['hit_r'],36)
                    for b in geo['nodes']:
                        self.assertAlmostEqual(a['r']**2/b['r']**2,a['count']/b['count'])
                        if a!=b:self.assertGreater(math.hypot(a['x']-b['x'],a['y']-b['y'])-a['hit_r']-b['hit_r'],0)

    def test_capacity_compacts_sparse_days_and_mobile_height(self):
        optimized=layout.optimize(self.source(4))
        f=optimized['frames'][0]
        self.assertEqual(f['model']['desktop']['capacity'],3)
        self.assertEqual(f['model']['mobile']['capacity'],3)
        self.assertEqual(f['model']['mobile']['height'],340)
        self.assertEqual(f['model']['mobile']['nodes'][2]['x'],200)

    def test_persistent_sector_positions_and_scales_do_not_change_on_reorder(self):
        self.f.fixture.tag('A',1,2);self.f.fixture.tag('B',3,21);self.f.fixture.tag('C',4)
        self.f.run_at();self.f.run_at('09:30',codes=tuple(range(2,22)))
        source=self.fixture.data()
        result=layout.optimize(source)
        self.assertNotEqual([g['name'] for g in result['frames'][0]['focus']['top']],
                            [g['name'] for g in result['frames'][1]['focus']['top']])
        for key in ('desktop','mobile'):
            a={n['name']:n for n in result['frames'][0]['model'][key]['nodes']}
            b={n['name']:n for n in result['frames'][1]['model'][key]['nodes']}
            for name in a.keys() & b.keys():
                self.assertEqual((a[name]['x'],a[name]['y']),(b[name]['x'],b[name]['y']))
            self.assertEqual(a['C']['r'],b['C']['r'])
            self.assertAlmostEqual(a['A']['r']**2/b['A']['r']**2,2)

    def test_top3_uses_existing_top10_tiebreak_and_default_detail(self):
        for name,code in [('가',11),('나',12),('다',6),('라',7)]:self.f.fixture.tag(name,code)
        self.f.run_at();self.f.run_at('09:30')
        result=layout.optimize(self.fixture.data());frame=result['frames'][-1]
        self.assertEqual([g['name'] for g in frame['focus']['top']],['다','라','가'])
        self.assertEqual(len(frame['model']['groups']),4)
        self.assertIn('TOP3 밖 1',layout.frame_html(frame))

    def test_top2_emphasis_does_not_weight_circle_area(self):
        frame=layout.optimize(self.source(4))['frames'][-1]
        for key in ('desktop','mobile'):
            self.assertEqual([n['rank'] for n in frame['model'][key]['nodes']],[1,2,3])
            self.assertEqual(len({n['r'] for n in frame['model'][key]['nodes']}),1)
        html=layout.frame_html(frame)
        self.assertIn('TOP1 · 우선',html);self.assertIn('TOP2 · 우선',html);self.assertIn('TOP3 · 보조',html)
        self.assertEqual(html.count('class="focus-card"'),2)

    def test_top3_overlap_is_reported_without_merging_tags(self):
        self.f.fixture.tag('반도체',1,2,3);self.f.fixture.tag('반도체 소부장',1,2);self.f.fixture.tag('바이오',4)
        self.f.run_at();before=self.f.dump();frame=layout.optimize(self.fixture.data())['frames'][0]
        self.assertEqual(len(frame['focus']['top']),3)
        self.assertEqual(frame['focus']['overlap'][0]['shared_codes'],['000001','000002'])
        self.assertEqual(len(frame['model']['groups']),3)
        self.assertEqual(before,self.f.dump())
        self.assertIn('TOP3 종목 중복 1쌍',layout.frame_html(frame))

    @unittest.skipUnless(browser_path(),'Chromium not installed')
    def test_browser_top2_card_selects_representatives_and_default_is_top1(self):
        for name,code in [('가',11),('나',12),('다',6),('라',7)]:self.f.fixture.tag(name,code)
        self.f.run_at();self.f.run_at('09:30')
        result=layout.optimize(self.fixture.data());top=result['frames'][-1]['focus']['top']
        with browser_page(layout.render(result),self.output.parent,width=390,height=844) as (command,evaluate,click):
            doc="document.getElementById('frame-1').contentDocument"
            self.assertTrue(evaluate(doc+f'.getElementById("pick-{top[0]["id"]}").checked'))
            self.assertEqual(evaluate(doc+'.querySelectorAll(".sector-choice:checked").length'),1)
            point=evaluate('(()=>{const f=document.getElementById("frame-1"),r=f.contentDocument.querySelectorAll(".focus-card")[1].getBoundingClientRect(),a=f.getBoundingClientRect();return{x:r.x+a.x+r.width/2,y:r.y+a.y+r.height/2}})()')
            command('Input.dispatchTouchEvent',dict(type='touchStart',touchPoints=[point]));command('Input.dispatchTouchEvent',dict(type='touchEnd',touchPoints=[]))
            self.assertTrue(evaluate(doc+f'.getElementById("pick-{top[1]["id"]}").checked'))
            self.assertIn('종목 7',evaluate(doc+f'.getElementById("detail-{top[1]["id"]}").textContent'))

    def test_original_timeline_renderer_default_remains_unchanged(self):
        source=self.source(4)
        self.assertEqual(timeline.render(source),timeline.render(source,frame_renderer=timeline.frame_html))
        self.assertNotIn('all-sectors',timeline.frame_html(source['frames'][0]))
        self.assertIn('all-sectors',layout.frame_html(layout.optimize(source)['frames'][0]))

    def test_guide_lists_and_native_detail_link_are_preserved(self):
        data=layout.optimize(self.source(15));html=layout.frame_html(data['frames'][-1])
        self.assertIn('class="reading-guide"',html)
        self.assertIn('class="all-sectors"',html)
        self.assertIn('href="about:srcdoc#selected-detail"',html)
        self.assertIn('id="selected-detail"',html)
        self.assertNotIn('<script',html)
        self.assertIn('기존 v1 진단',html)

    def test_empty_timeline_and_all_unclassified(self):
        self.assertIn('성공한 운영 관측이 없습니다',layout.render(layout.optimize(self.fixture.data())))
        self.f.run_at();self.assertIn('미분류 &lt;b&gt;20',layout.render(layout.optimize(self.fixture.data())))

    def test_readonly_export_and_existing_file_protection(self):
        self.source(4);before=self.f.dump()
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')):
            result=layout.export(self.path,'2026-10-20','mock',self.output)
        self.assertEqual(before,self.f.dump());self.assertEqual(result['collections'],2)
        for path in (self.path,self.output,self.output.with_suffix('.csv')):
            with self.assertRaises(ValueError):layout.export(self.path,'2026-10-20','mock',path)

    def test_actual_cli_exports_operating_query_one(self):
        self.source(1)
        env=os.environ.copy();env.update(PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1')
        args=[sys.executable,'-B','-m','kiwoom_collector.bubble_layout','--db',str(self.path),'--date','2026-10-20','--output',str(self.output)]
        result=subprocess.run(args,env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['qry_tp'],'1')

    @unittest.skipUnless(browser_path(),'Chromium not installed')
    def test_browser_mobile_hidden_sector_selection_jump_and_no_horizontal_scroll(self):
        data=layout.optimize(self.source(20))
        with browser_page(layout.render(data),self.output.parent,width=390,height=844) as (command,evaluate,click):
            frame=data['frames'][-1];hidden=frame['model']['remainder'][-1]
            doc="document.getElementById('frame-1').contentDocument"
            self.assertLessEqual(evaluate('document.body.scrollWidth'),390)
            self.assertLessEqual(evaluate(doc+'.body.scrollWidth'),evaluate("document.getElementById('frame-1').clientWidth"))
            def native(selector):
                evaluate(doc+f'.querySelector({json.dumps(selector)}).scrollIntoView({{block:"center"}})')
                p=evaluate('(()=>{const f=document.getElementById("frame-1"),r=f.contentDocument.querySelector('+json.dumps(selector)+').getBoundingClientRect(),a=f.getBoundingClientRect();return{x:r.x+a.x+r.width/2,y:r.y+a.y+r.height/2}})()')
                command('Input.dispatchTouchEvent',dict(type='touchStart',touchPoints=[p]));command('Input.dispatchTouchEvent',dict(type='touchEnd',touchPoints=[]))
            native('.all-sectors>summary')
            native('label[for=pick-'+hidden['id']+']')
            self.assertTrue(evaluate(doc+f'.getElementById("pick-{hidden["id"]}").checked'))
            self.assertEqual(evaluate(doc+f'.defaultView.getComputedStyle({doc}.getElementById("detail-{hidden["id"]}")).display'),'block')
            native('.detail-jump')
            self.assertTrue(evaluate(doc+'.location.hash==="#selected-detail"'))
            self.assertTrue(evaluate(doc+f'.getElementById("pick-{hidden["id"]}").checked'))

    @unittest.skipUnless(browser_path(),'Chromium not installed')
    def test_browser_extreme_sizes_long_names_and_minimum_targets(self):
        html=layout.render(layout.optimize(self.source(8,extreme=True)))
        for width,height in ((1360,900),(390,844),(320,844)):
            with browser_page(html,self.output.parent,width=width,height=height) as (command,evaluate,click):
                stats=json.loads(evaluate('''(()=>{const f=document.getElementById('frame-1'),d=f.contentDocument,map=d.querySelector(innerWidth<=760?'.mobile-map':'.desktop-map'),r=map.getBoundingClientRect();
                return JSON.stringify({w:d.body.scrollWidth,cw:f.clientWidth,labels:Array.from(map.querySelectorAll('.bubble-label')).map(e=>{const a=e.getBoundingClientRect(),n=e.querySelector('.name').getBoundingClientRect();return{hit:Math.min(a.width,a.height),inside:n.left>=r.left&&n.right<=r.right&&n.top>=r.top&&n.bottom<=r.bottom}})});})()'''))
                self.assertLessEqual(stats['w'],stats['cw'])
                for mark in stats['labels']:
                    self.assertGreaterEqual(mark['hit'],44)
                    self.assertTrue(mark['inside'],stats)

    @unittest.skipUnless(browser_path(),'Chromium not installed')
    def test_browser_medium_mark_boundary_two_line_name_has_no_clip(self):
        self.f.fixture.tag('최대관심군',*range(1,19))
        self.f.fixture.tag('두줄이상길게표시되는섹터이름',1,2,3,4)
        self.f.run_at();self.f.run_at('09:30')
        data=layout.optimize(self.fixture.data())
        medium=next(n for n in data['frames'][-1]['model']['mobile']['nodes'] if n['count']==4)
        self.assertGreaterEqual(medium['r'],38);self.assertLess(medium['r'],40)
        with browser_page(layout.render(data),self.output.parent,width=390,height=844) as (command,evaluate,click):
            self.assertTrue(evaluate("(()=>{const d=document.getElementById('frame-1').contentDocument,l=d.querySelector('.mobile-map .node-"+medium['id']+" label');return l.scrollHeight<=l.clientHeight&&l.scrollWidth<=l.clientWidth})()"))


if __name__=='__main__':unittest.main()
