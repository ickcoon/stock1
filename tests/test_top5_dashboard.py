"""Saved Top5 HTML integration; no network or operational database."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from copy import deepcopy
from kiwoom_collector import eligibility_dashboard as ui
from kiwoom_collector.top5_dashboard import top5_section
from kiwoom_collector.daily_chart import fingerprint
from kiwoom_collector.analyst.card import chart_card
from kiwoom_collector.analyst.context import analyze_context
from test_weekly_hybrid import weekly_fixture
from test_eligibility_dashboard import build


class Top5DashboardTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('external network forbidden')))
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        items=[]
        for n in range(1,6):
            code=f'{n:06d}';folder=self.root/code;folder.mkdir()
            doc,_,_=weekly_fixture();doc['stock_code']=code
            card=chart_card(analyze_context(doc));(folder/'card.json').write_text(json.dumps(card),encoding='utf-8')
            from PIL import Image
            Image.new('RGB',(10,6),'white').save(folder/'mini.png')
            items.append(dict(rank=n,stock_code=code,stock_name='SYNTHETIC '+code,card_path=code+'/card.json',
                mini_chart_path=code+'/mini.png',status='available',summary=['Provisional <script>unsafe()</script>']))
        self.report=dict(schema_version='arvis-c12-chart-top5-v1',source_kind='fixture',environment='mock',
            c12_as_of='2026-10-29T09:50:00+09:00',generated_at='2026-11-01T21:00:00+09:00',items=items)
        self.path=self.root/'top5.json';self.save()

    def save(self):self.path.write_text(json.dumps(self.report),encoding='utf-8')

    def test_five_cards_order_relative_images_demo_and_escaping(self):
        html=top5_section(self.path,self.root/'different folder'/'dashboard.html')
        self.assertEqual(html.count('class="top5-card"'),5)
        self.assertIn('DEMO / SYNTHETIC',html)
        self.assertIn('src="../000001/mini.png"',html)
        self.assertIn('target="_blank"',html)
        self.assertIn('data-code="000001"',html)
        self.assertLess(html.index('data-code="000001"'),html.index('data-code="000005"'))
        self.assertNotIn('<script>unsafe()',html);self.assertIn('&lt;script&gt;',html)
        self.assertIn('repeat(2,minmax(0,1fr))',html);self.assertIn('max-width:760px',html)

    def test_individual_missing_card_png_and_unsafe_path_do_not_drop_cards(self):
        (self.root/'000001/card.json').unlink();(self.root/'000002/mini.png').unlink()
        self.report['items'][2]['mini_chart_path']='../outside.png';self.save()
        html=top5_section(self.path,self.root/'dashboard.html')
        self.assertEqual(html.count('class="top5-card"'),5)
        self.assertIn('Analysis unavailable / failed',html);self.assertIn('PNG missing',html)
        self.assertNotIn('src="../outside.png"',html)
        self.assertIn('000005/mini.png',html)

    def test_existing_dashboard_unchanged_unless_top5_requested(self):
        bundle=build();original=ui.render(bundle)
        self.assertNotIn('id="c12-top5"',original)
        html=ui.render(bundle,top5_json=self.path,output_html=self.root/'dashboard.html')
        self.assertIn('id="c12-top5"',html)
        self.assertIn("img-src 'self';",html)
        restored=html.replace(top5_section(self.path,self.root/'dashboard.html'),'').replace(" img-src 'self';",'')
        self.assertEqual(restored,original)
