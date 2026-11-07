import json
from html import escape
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest
from contextlib import nullcontext
from unittest.mock import patch
from kiwoom_collector import c16_sessions as sessions


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.folder=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root=self.folder/'reports';self.root.mkdir()

    def write(self,path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf8')

    def test_missing_and_existing_hold_are_empty_without_reconstruction(self):
        data=sessions.afternoon_data([self.root],'2026-11-05','14:30')
        self.assertEqual(data['status'],'missing')
        self.write(self.root/'workflow/2026-11-05/1430.json',dict(review=dict(reason='source_market_time_unverified')))
        data=sessions.afternoon_data([self.root],'2026-11-05','14:30')
        with patch.object(sessions,'pm_chart') as chart:
            html=sessions.pm_frame(data,self.folder)
        chart.assert_not_called()
        self.assertIn('분석 데이터 없음',html);self.assertIn('기존 분석 보류',html)

    def test_saved_candidates_keep_original_selection_order_and_reasons(self):
        folder=self.root/'c12x-20261104-1430'
        report=dict(selection_type='PM_CLOSING_REVIEW',source_kind='readonly_adapter',environment='real',trade_date='2026-11-04',selection_cutoff_at='2026-11-04T14:30:00+09:00',pm_candidates=[dict(stock_code='005380',qualification=dict(review_status='기존 저장 근거'))],observation=dict(stocks=[dict(stock_code='005380',stock_name='현대차',change_percent='2.5',turnover_million_krw='10000')]))
        self.write(folder/'telegram.json',report)
        self.write(folder/'step6d.json',dict(rows=[dict(stock_code='005380',sectors=['자동차'],market_cap_won_verified='1000000000000')]))
        data=sessions.afternoon_data([self.root],'2026-11-04','14:30')
        self.assertEqual(data['status'],'available')
        self.assertEqual([row['stock_code'] for row in data['candidates']],['005380'])
        with patch.object(sessions,'pm_chart',return_value='005380/mini.png'),patch.object(sessions,'cached_summary',return_value=dict(companies={})):
            html=sessions.pm_frame(data,self.folder)
        self.assertIn('현대차',html);self.assertNotIn('기존 저장 근거',html)
        self.assertEqual(data['candidates'][0]['qualification']['review_status'],'기존 저장 근거')
        self.assertIn('005380/mini.png',html);self.assertIn('거래대금 100억',html);self.assertIn('시총',html)
        self.assertEqual(sessions.afternoon_data([self.root],'2026-11-04','15:00')['status'],'missing')
        self.assertEqual(sessions.afternoon_day([self.root],'2026-11-05'),'2026-11-04')

    def test_tabs_preserve_morning_html_and_have_two_empty_pm_slots(self):
        frames={slot:'<!doctype html><p>saved '+slot+'</p><img src="mini.png">' for slot in ('09:40','09:45','09:50')}
        original=''.join('<iframe title="2026-11-05 '+slot+':00" srcdoc="'+escape(html,quote=True)+'"></iframe>' for slot,html in frames.items())
        with patch('socket.socket.connect',side_effect=AssertionError('network forbidden')):
            html=sessions.session_dashboard(original,day='2026-11-05',reports_roots=[self.root],output=self.folder,snapshot_root=self.folder/'archive')
        class Frames(HTMLParser):
            def __init__(self):super().__init__();self.frames=[]
            def handle_starttag(self,tag,attrs):
                if tag=='iframe':self.frames.append(dict(attrs))
        parsed=Frames();parsed.feed(html)
        self.assertEqual(len(parsed.frames),6)
        self.assertEqual([frame['srcdoc'] for frame in parsed.frames[:3]],list(frames.values()))
        self.assertTrue(all('분석 데이터 없음' in frame['srcdoc'] for frame in parsed.frames[3:5]))
        self.assertIn('id="am-0950" checked',html)
        self.assertIn('id="session-am" checked',html)
        self.assertNotIn('<script',html)

    def test_archived_replay_reuses_existing_engines_without_promoting_holds(self):
        from kiwoom_collector import cross_adapter,eligibility_dashboard
        class DB:
            def execute(self,*args):return [dict(id='saved',collected_at='2026-11-05T14:30:00+09:00',record_count=20)]
        pair=dict(ranking=dict(rows=[dict(raw_json=json.dumps(dict(dt='20261105',tm='142900')))]),
                  turnover=dict(rows=[{},{}],request_started_at='2026-11-05T14:30:01+09:00',status='partial',pagination_complete=False,test_mode=True))
        review=dict(rows=[dict(stock_code='005380',filter_selected=True,candidate_confirmed=False,
                              qualification=dict(final_review_allowed=False,review_status='검증 보류',reasons=['market_cap_missing']))])
        with patch.object(Path,'is_file',return_value=True),patch.object(cross_adapter,'readonly_database',return_value=nullcontext((DB(),{}))),patch.object(cross_adapter,'adapt',return_value=pair) as adapt,patch.object(eligibility_dashboard,'build',return_value=dict(eligibility=review)) as build,patch('kiwoom_collector.c15_leadership.reference_afternoon',return_value=dict(candidates=[],sectors=[])):
            data=sessions.replay_afternoon(self.root,'2026-11-05','14:30')
        adapt.assert_called_once();build.assert_called_once()
        self.assertEqual(data['status'],'replayed');self.assertEqual(data['candidates'],[])
        self.assertEqual(data['replay']['prefilter_count'],1)
        self.assertEqual(data['replay']['source_stamps'],['2026-11-05 14:29:00'])
        html=sessions.pm_frame(data,self.folder)
        self.assertNotIn('분석 데이터 없음',html);self.assertIn('검증 보류',html)
