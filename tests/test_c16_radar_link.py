"""Focused saved connection checks, not another engine regression suite."""
from html.parser import HTMLParser
from html import unescape
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'diagnostics/c16-a-20261106-final'
NEW=ROOT/'diagnostics/c16-b-connected-20261105-final'


class Frames(HTMLParser):
    def __init__(self,text):
        super().__init__();self.frames={};self.controls=[];self.feed(text)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='iframe':self.frames[a['data-collection']]=a['srcdoc']
        if tag=='input' and a.get('name')=='observation':self.controls.append(a['id'])


class ConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (NEW/'dashboard.html').exists():raise unittest.SkipTest('local saved artifacts unavailable')
        cls.old=Frames((OLD/'dashboard.html').read_text(encoding='utf8'))
        cls.new=Frames((NEW/'dashboard.html').read_text(encoding='utf8'))
        cls.audit=json.loads((NEW/'connection-audit.json').read_text(encoding='utf8'))

    def test_existing_time_controls_and_collections_preserved(self):
        self.assertEqual(self.old.controls,self.new.controls)
        self.assertEqual(set(self.old.frames),set(self.new.frames))

    def test_exact_c15_order_and_missing_chart_not_substituted(self):
        for slot in ('0940','0945','0950'):
            source=json.loads((ROOT/f'diagnostics/c15-12-20261105/reports/c15-20261105-{slot}/diagnostics.json').read_text(encoding='utf8'))
            expected=[s['stock_code'] for sector in source['analysis']['sectors'] for s in sector['leaders'][:3]]
            items=json.loads((NEW/(slot+'-cards.json')).read_text(encoding='utf8'))['items']
            self.assertEqual([s['stock_code'] for s in items],expected)
            if slot!='0950':self.assertIsNone(next(s for s in items if s['stock_code']=='068270')['mini_chart_path'])

    def test_mapping_changes_only_references_not_top20_or_entry_exit_counts(self):
        self.assertEqual([(a['slot'],a['C15_reference_unclassified']) for a in self.audit[-4:]],[('09:35',6),('09:40',7),('09:45',6),('09:50',5)])
        from kiwoom_collector import cross_adapter,eligibility_dashboard
        raw=json.loads((ROOT/'diagnostics/c15-12-20261105/reports/c15-20261105-0950/diagnostics.json').read_text(encoding='utf8'))['diagnostics']['source_report']
        at=raw['analysis']['snapshot_selection']['selected']['0198']['snapshot']['collection_started_at']
        original=eligibility_dashboard.build(cross_adapter.adapt(raw['paths']['ranking'],raw['paths']['turnover'],ranking_at=at,environment='real'))
        for a,frame in zip(self.audit,original['timeline']['frames']):
            self.assertEqual(a['top20'],[r['stk_cd'] for r in frame['data']['current']['stocks']])
            # Existing entrant/exit labels remain in the selected frame renderer.
            for field in ('entrants','exits'):
                for stock in frame['data'][field] or []:self.assertTrue(stock['stk_nm'] in unescape(self.new.frames[frame['collection_id']]),stock['stk_cd'])

    def test_frame_local_charts_safe_assets_and_no_future_selection_on_0935(self):
        for a in self.audit:
            html=self.new.frames[a['collection_id']]
            if a['c15_snapshot_available']:
                self.assertEqual(html.count('class="top5-card"'),9)
                self.assertIn("img-src 'self'",html)
                self.assertIn('후속 참고 분석',html)
                self.assertIn('C15 상승 주도섹터 TOP3',html)
            else:self.assertNotIn('class="top5-card"',html)
        for slot in ('0940','0945','0950'):
            for item in json.loads((NEW/(slot+'-cards.json')).read_text(encoding='utf8'))['items']:
                if item.get('mini_chart_path'):self.assertTrue((NEW/item['mini_chart_path']).is_file())
