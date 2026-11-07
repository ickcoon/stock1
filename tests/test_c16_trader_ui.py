"""Focused C16-D presentation checks using existing saved results."""
from html.parser import HTMLParser
import json
from pathlib import Path
import unittest
from kiwoom_collector.c16_ui import momentum_board

ROOT=Path(__file__).resolve().parents[1]/'diagnostics'


class View(HTMLParser):
    def __init__(self,text):
        super().__init__();self.frames=[];self.controls=[];self.details=0;self.visible_tables=0;self.images=0;self.cards=0;self.feed(text)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='iframe':self.frames.append(a['srcdoc'])
        if tag=='input' and a.get('name')=='observation':self.controls.append(a['id'])
        if tag=='details':self.details+=1
        if tag=='table' and not self.details:self.visible_tables+=1
        if tag=='img':self.images+=1
        if tag=='article' and a.get('class')=='top5-card':self.cards+=1
    def handle_endtag(self,tag):
        if tag=='details':self.details-=1


class TraderUiTests(unittest.TestCase):
    def test_saved_time_controls_and_nine_cards(self):
        old=View((ROOT/'c16-b-connected-20261105-final/dashboard.html').read_text(encoding='utf8'))
        new=View((ROOT/'c16-d-trader-20261105/dashboard.html').read_text(encoding='utf8'))
        self.assertEqual(old.controls,new.controls)
        last=new.frames[-1];view=View(last)
        self.assertEqual(view.cards,9);self.assertEqual(view.images,9);self.assertEqual(view.visible_tables,0)
        self.assertLess(last.index('trader-top3'),last.index('trader-momentum'))
        self.assertLess(last.index('trader-momentum'),last.index('<section id="c12-top5"'))
        self.assertLess(last.index('<section id="c12-top5"'),last.index('<details class="trader-details"'))
        self.assertIn('aspect-ratio:960/370',last);self.assertIn('object-fit:contain',last)
        self.assertIn('compact-close',last)

    def test_original_chart_and_analysis_assets_preserved(self):
        source=ROOT/'c16-b-connected-20261105-final';target=ROOT/'c16-d-trader-20261105'
        for path in source.glob('*/card.json'):self.assertEqual(path.read_bytes(),(target/path.relative_to(source)).read_bytes())
        for path in source.glob('*/mini.png'):self.assertEqual(path.read_bytes(),(target/path.relative_to(source)).read_bytes())

    def test_explicit_parent_preference_without_summing(self):
        def g(name,codes):return dict(name=name,count=len(codes),delta20=0,current=dict(stocks=[dict(stk_cd=c) for c in codes]))
        html=momentum_board(dict(groups=[g('보안',['1','2']),g('정보보안',['1']),g('은행',['1'])],previous=True,entrants=[],retained=[1,2],exits=[]))
        rows=html.split('<details>',1)[0]
        self.assertIn('보안',rows);self.assertNotIn('정보보안',rows)
        self.assertIn('은행',rows);self.assertIn('2종목',rows);self.assertNotIn('3종목',rows)
        self.assertIn('정보보안',html)
