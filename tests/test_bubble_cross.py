"""Saved JSON panel, chronology, safe amounts and real browser interactions."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from bubble_browser_helpers import browser_page
from kiwoom_collector import bubble_cross as ui


def source():
    base=json.loads((Path(__file__).parent/'fixtures/cross_analysis_synthetic.json').read_text(encoding='utf8'))
    a,b=base['ranking'],base['turnover']
    a['test_mode']=False
    b['request_started_at']=b['collected_at'];b['response_received_at']=b['collected_at'];b['pagination_complete']=0;b['status']='partial'
    b['rows'][0]['trde_prica']='75000'
    for h in base['history']:
        h.update(api_id='ka00198',test_mode=False)
    base['sectors']={c:[dict(sector_name=n,source='synthetic',updated_at='2026-10-27') for n in s['sectors']] for c,s in base['securities'].items()}
    base['sectors']['000660'].append(dict(sector_name='긴이름섹터'*15,source='synthetic'))
    base['code_registry']={}
    return base


class BubbleCrossTests(unittest.TestCase):
    def test_build_no_input_mutation_or_geometry_changes(self):
        d=source();before=deepcopy(d);b=ui.build(d)
        self.assertEqual(d,before)
        for f in b['timeline']['frames']:
            geo=deepcopy(f['model']['desktop'])
            ui.frame_html(f,b)
            self.assertEqual(geo,f['model']['desktop'])
        self.assertEqual(len(b['timeline']['frames'][-1]['focus']['top']),3)

    def test_no_future_finance_in_earlier_frame(self):
        b=ui.build(source())
        old=ui.frame_html(b['timeline']['frames'][0],b)
        self.assertIn('15:03 자료를 과거에 소급하지 않습니다',old)
        self.assertNotIn('750.00억원',old)
        self.assertIn('750.00억원',ui.frame_html(b['timeline']['frames'][-1],b))

    def test_unknown_cap_close_amount_and_candidate_remain_unknown(self):
        b=ui.build(source());html=ui.frame_html(b['timeline']['frames'][-1],b)
        self.assertIn('시총 3,000억원 이상<b>미확인</b>',html)
        self.assertIn('장 마감 누적 1,000억원 이상: <b>미확인</b>',html)
        self.assertIn('candidate_confirmed=false',html)
        self.assertIn('cash-1000" checked',html)

    def test_amount_scales_and_invalid_missing(self):
        self.assertEqual(ui.money('2445916000000'),'2.45조원')
        self.assertEqual(ui.money('50000000000'),'500.00억원')
        for value in (None,'bad','-1'):self.assertEqual(ui.money(value),'미확인')

    def test_sector_filter_auxiliary_and_hostile_names(self):
        d=source();d['ranking']['rows'][0]['stk_nm']='<script>alert(1)</script>'
        b=ui.build(d);html=ui.frame_html(b['timeline']['frames'][-1],b)
        self.assertIn('&lt;script&gt;',html)
        self.assertNotIn('<script>',html)
        self.assertIn('TOP20 보조 관찰',html)
        self.assertIn('pick-unclassified',html)
        self.assertIn('cash500',html)

    def test_environment_mismatch_blocked(self):
        d=source();d['turnover']['environment']='real'
        with self.assertRaises(ValueError):ui.build(d)

    def test_outside_300_seconds_never_shows_finance_as_current(self):
        d=source();d['turnover']['request_started_at']='2026-10-27T14:05:01+09:00'
        b=ui.build(d);html=ui.frame_html(b['timeline']['frames'][-1],b)
        self.assertIn('시차 300초 기준 미충족',html)
        self.assertNotIn('750.00억원',html)

    def test_new_output_only_and_no_db_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'report.html'
            ui.export(source(),p)
            with self.assertRaises(ValueError):ui.export(source(),p)
            with self.assertRaises(ValueError):ui.export(source(),p.with_suffix('.db'))
        with self.assertRaises(SystemExit):ui.main(['--input-json','stock1.db','--output','x.html'])

    def test_desktop_mobile_real_browser_selection_filter_timeline_overflow(self):
        html=ui.render(ui.build(source()))
        for width,height in ((1360,900),(390,844)):
            with self.subTest(width=width),tempfile.TemporaryDirectory() as folder:
                with browser_page(html,folder,width=width,height=height) as (command,ev,click):
                    def inner(js):return ev('(()=>{const d=document.querySelector("#frame-1").contentDocument;'+js+'})()')
                    self.assertTrue(inner('return d.querySelector("#cash-1000").checked;'))
                    ev('document.querySelector("#frame-1").contentDocument.querySelector("#cash-500").click()')
                    self.assertTrue(inner('return d.querySelector("#cash-500").checked;'))
                    self.assertTrue(inner('return [...d.querySelectorAll(".cash500 .stock-card")].some(x=>x.textContent.includes("750.00억원"));'))
                    ev('document.querySelector("#frame-1").contentDocument.querySelector("#pick-unclassified").click()')
                    self.assertTrue(inner('return getComputedStyle(d.querySelector("#cross-unclassified")).display!=="none";'))
                    self.assertTrue(inner('return d.documentElement.scrollWidth<=d.documentElement.clientWidth+1;'))
                    ev('document.querySelector("#time-0").click()')
                    self.assertEqual(ev('getComputedStyle(document.querySelector("#frame-0")).display'),'block')
                    self.assertIn('이 회차에 결합된',ev('document.querySelector("#frame-0").contentDocument.querySelector(".cross-section").textContent'))


if __name__=='__main__':unittest.main()
