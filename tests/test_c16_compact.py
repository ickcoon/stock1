"""Small presentation-only checks against saved C15/C16 cards."""
import json
from pathlib import Path
import re
import unittest
from kiwoom_collector.top5_dashboard import top5_section,compact_summary,COMPACT_CSS

ROOT=Path(__file__).resolve().parents[1]/'diagnostics/c16-c-compact-20261105'


class CompactTests(unittest.TestCase):
    def test_four_lines_and_no_development_bullets(self):
        if not ROOT.exists():self.skipTest('local artifact unavailable')
        html=top5_section(ROOT/'0950-cards.json',ROOT/'dashboard.html',limit=9)
        cards=re.findall(r'<article class="top5-card".*?</article>',html,re.S)
        self.assertEqual(len(cards),9)
        for card in cards:
            self.assertNotIn('<li>',card)
            for word in ('시총','거래대금','후속 참고 분석','Top5 appearances','MA20 above'):self.assertNotIn(word,card)
            summary=card.split('<div class="compact-summary">',1)[1]
            self.assertEqual(summary.count('<p'),4)
            for word in ('Resistance','Support','저항 거리','지지 거리','C15 sector rank','leader rank'):self.assertIn(word,card)

    def test_no_analysis_or_image_changes(self):
        if not ROOT.exists():self.skipTest('local artifact unavailable')
        before=ROOT.parent/'c16-b-connected-20261105-final'
        for path in before.rglob('*'):
            if path.is_file() and path.suffix in ('.png','.json'):self.assertEqual(path.read_bytes(),(ROOT/path.relative_to(before)).read_bytes())
        self.assertIn('aspect-ratio:960/462',COMPACT_CSS)
        self.assertIn('object-fit:contain',COMPACT_CSS)

    def test_unknown_values_stay_unknown(self):
        html=compact_summary(None)
        self.assertIn('Resistance 미확인',html);self.assertIn('Support 미확인',html)
        self.assertIn('데이터 부족',html)
