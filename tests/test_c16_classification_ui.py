"""Focused corporate/theme attribution and three-column UI checks."""
from copy import deepcopy
from html.parser import HTMLParser
import json
from pathlib import Path
import unittest
from kiwoom_collector.c16_display import grouped_snapshot
from kiwoom_collector.top5_dashboard import default_chart_items,COMPACT_CSS

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'diagnostics/c16-f-classified-20261105-final'


class ClassificationTests(unittest.TestCase):
    def test_actual_group_and_independent_robot_evidence(self):
        source=json.loads((ROOT/'diagnostics/c15-12-20261105/reports/c15-20261105-0950/diagnostics.json').read_text(encoding='utf8'))
        before=deepcopy(source);view=grouped_snapshot(source)
        self.assertEqual(source,before)
        self.assertEqual([s['name'] for s in view['analysis']['sectors']],['제약바이오','현대차그룹/자동차','신재생에너지'])
        robot=next(s for s in view['display_ranking']['held_groups'] if s['name']=='로봇')
        self.assertEqual(set(robot['original_rising_stock_codes']),{'005380','012330'})
        self.assertEqual(robot['independent_stock_codes'],[])
        for code in ('005380','000270','012330'):self.assertEqual(view['display_ranking']['primary_display_ownership'][code],'현대차그룹/자동차')
        auto=view['analysis']['sectors'][1]
        self.assertEqual(auto['turnover_eok'],'2090.49');self.assertFalse(auto['catalyst']['verified'])
        codes=[c for s in view['display_ranking']['groups'] for c in s['rising_stock_codes']]
        self.assertEqual(len(codes),len(set(codes)))

    def test_general_other_corporate_group_and_independent_theme(self):
        def sector(name,rank,codes):return dict(name=name,rank=rank,rising_stock_codes=codes,leaders=[dict(stock_code=c) for c in codes])
        sectors=[sector('다른 그룹',1,['1','2']),sector('복수 테마',2,['1','2','3','4'])]
        source=dict(reference_at='2025-03-04T09:50:00+09:00',analysis=dict(sectors=sectors,ranked_sectors=sectors),
            diagnostics=dict(stock_measurements={c:dict(turnover=dict(eok='10')) for c in ['1','2','3','4']}))
        view=grouped_snapshot(source);themes={s['name']:s for s in view['display_ranking']['groups']}
        self.assertEqual(themes['복수 테마']['rising_stock_codes'],['3','4'])
        self.assertEqual(themes['복수 테마']['turnover_eok'],'20')
        self.assertEqual(themes['다른 그룹']['catalyst']['status'],'상승 재료 미확인')

    def test_saved_order_nine_unique_charts_and_responsive_columns(self):
        groups=json.loads((OUT/'0950-display-ranking.json').read_text(encoding='utf8'))['groups'][:3]
        items=json.loads((OUT/'0950-cards.json').read_text(encoding='utf8'))['items']
        visible,remaining=default_chart_items(items)
        self.assertEqual(len(visible),9);self.assertFalse(remaining)
        self.assertEqual(len({i['stock_code'] for i in visible}),9)
        self.assertEqual([i['stock_code'] for i in visible],[s['stock_code'] for g in groups for s in g['leaders']])
        self.assertIn('repeat(3,minmax(0,1fr))',COMPACT_CSS)
        self.assertIn('max-width:1100px',COMPACT_CSS);self.assertIn('max-width:650px',COMPACT_CSS)
        for item in visible:self.assertTrue((OUT/item['mini_chart_path']).is_file())
