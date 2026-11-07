"""Focused optional-news cutoff and final card UI checks."""
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from kiwoom_collector import c16_news

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'diagnostics/c16-g-news-20261105-final'


class NewsUiTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(c16_news,'SEARCH_CACHE',ROOT/'diagnostics/nonexistent-test-news-cache.json'))

    def test_reviewed_article_before_cutoff_only(self):
        leaders=[dict(stock_code='005380',name='현대차'),dict(stock_code='000270',name='기아')]
        early=c16_news.cached_summary('현대차그룹/자동차',leaders,'2026-11-05T09:28:00+09:00')
        self.assertEqual(early['summary'],'공통 특징주 재료 없음')
        valid=c16_news.cached_summary('현대차그룹/자동차',leaders,'2026-11-05T09:50:00+09:00')
        self.assertIn('관세 완화 기대',valid['summary'])
        self.assertTrue(valid['common_articles'])

    def test_late_news_and_single_stock_related_news_not_common_driver(self):
        renewable=c16_news.cached_summary('신재생에너지',[dict(stock_code='010060'),dict(stock_code='112610')],'2026-11-05T09:50:00+09:00')
        self.assertEqual(renewable['summary'],'공통 특징주 재료 없음')
        self.assertTrue(any(r['reason']=='published_after_analysis' for r in renewable['rejected']))
        bio=c16_news.cached_summary('제약바이오',[dict(stock_code='128940'),dict(stock_code='196170')],'2026-11-05T09:50:00+09:00')
        self.assertEqual(bio['summary'],'공통 특징주 재료 없음')

    def test_search_and_cache_failures_are_nonfatal(self):
        with patch.object(c16_news,'urlopen',side_effect=TimeoutError):
            self.assertEqual(c16_news.search_public('테마',['합성종목'],'2026-11-05T09:50:00+09:00')['status'],'unavailable')
        with patch.object(c16_news,'CACHE',ROOT/'diagnostics/nonexistent-news-cache.json'):
            self.assertEqual(c16_news.cached_summary('테마',[],'2026-11-05T09:50:00+09:00')['summary'],'공통 특징주 재료 없음')

    def test_saved_minimal_cards_and_real_leader_line(self):
        if not OUT.exists():self.skipTest('artifact not generated')
        class Frames(HTMLParser):
            def __init__(self,text):super().__init__();self.frames=[];self.feed(text)
            def handle_starttag(self,tag,attrs):
                if tag=='iframe':self.frames.append(dict(attrs)['srcdoc'])
        last=Frames((OUT/'dashboard.html').read_text(encoding='utf8')).frames[-1]
        self.assertIn('한미약품 | +8.97% | 387억 | 5.29조',last)
        self.assertIn('장중 관측',last);self.assertIn('저장 일봉 2026-11-04',last)
        for article in re.findall(r'<article class="top5-card".*?</article>',last,re.S):
            self.assertNotIn('compact-close',article);self.assertNotIn('compact-rank',article)
            self.assertNotIn('<small>',article);self.assertNotIn('<details',article)
            self.assertIn('전고점 이격',article)
            self.assertNotIn('Resistance',article)
