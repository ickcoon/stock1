"""Focused C16-E grouping and presentation checks only."""
from copy import deepcopy
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import unittest
from kiwoom_collector.c16_display import grouped_snapshot

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'diagnostics/c16-e-ranked-20261105'


class FinalUiTests(unittest.TestCase):
    def test_grouping_unique_amounts_and_original_unchanged(self):
        def s(name,rank,codes):return dict(name=name,rank=rank,rising_stock_codes=codes,leaders=[dict(stock_code=c) for c in codes])
        sectors=[s('신재생에너지',1,['1','2']),s('태양광/신재생',2,['2','3']),s('반도체',3,['4']),s('MLCC(적층세라믹콘덴서)',4,['4','5'])]
        snapshot=dict(analysis=dict(sectors=sectors[:3],ranked_sectors=sectors),diagnostics=dict(stock_measurements={str(i):dict(turnover=dict(eok=str(i*10))) for i in range(1,6)}))
        before=deepcopy(snapshot);view=grouped_snapshot(snapshot)
        self.assertEqual(snapshot,before)
        groups={g['name']:g for g in view['display_ranking']['groups']}
        self.assertEqual(groups['신재생에너지']['turnover_eok'],'60')
        self.assertEqual(groups['신재생에너지']['rising_count'],3)
        self.assertEqual(groups['반도체/MLCC']['turnover_eok'],'90')
        self.assertEqual(view['analysis']['sectors'][0]['name'],'반도체/MLCC')

    def test_saved_card_order_matches_display_board(self):
        for slot in ('0940','0945','0950'):
            view=json.loads((OUT/(slot+'-display-ranking.json')).read_text(encoding='utf8'))
            cards=json.loads((OUT/(slot+'-cards.json')).read_text(encoding='utf8'))
            expected=[s['stock_code'] for group in view['groups'][:3] for s in group['leaders']]
            self.assertEqual(expected,[i['stock_code'] for i in cards['items']])
            self.assertNotIn('은행',[g['name'] for g in view['groups'][:3]])
            for card in cards['items']:self.assertTrue((OUT/card['mini_chart_path']).is_file())

    def test_card_has_only_high_gap_and_verdict_after_chart(self):
        class Frames(HTMLParser):
            def __init__(self,text):super().__init__();self.frames=[];self.controls=[];self.feed(text)
            def handle_starttag(self,tag,attrs):
                a=dict(attrs)
                if tag=='iframe':self.frames.append(a['srcdoc'])
                if tag=='input' and a.get('name')=='observation':self.controls.append(a['id'])
        new=Frames((OUT/'dashboard.html').read_text(encoding='utf8'))
        old=Frames((ROOT/'diagnostics/c16-d-trader-20261105/dashboard.html').read_text(encoding='utf8'))
        self.assertEqual(new.controls,old.controls)
        articles=re.findall(r'<article class="top5-card".*?</article>',new.frames[-1],re.S)
        self.assertEqual(len(articles),8)
        for article in articles:
            self.assertIn('전고점 이격',article);self.assertIn('compact-close',article)
            self.assertNotIn('Resistance',article);self.assertNotIn('Support',article)
            self.assertNotIn('지지 거리',article);self.assertNotIn('<details',article)
            self.assertNotIn('후속 참고 분석',article)
