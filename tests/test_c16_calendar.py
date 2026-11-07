from copy import deepcopy
import hashlib
import base64
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from kiwoom_collector import c16_calendar as cal


def market():
    return dict(common=dict(domestic_previous=dict(KOSPI=None,KOSDAQ=None),nasdaq_previous=None,adr20={},issues=[]),
        observations=dict(domestic_current=dict(KOSPI=None,KOSDAQ=None),nasdaq_futures=None,domestic_night_futures=None),
        state='판단 자료 부족',summary=['[시장 환경] 판단 자료 부족','지수·선물 미수집'],explanation='지수 미수집',rule_version='test')


class CalendarTests(unittest.TestCase):
    def setUp(self):self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))/'archive'

    def record(self,at='2026-11-05T09:50:00+09:00'):
        return dict(analysis_at=at,origin='saved_result_restored',status='참고 후보',selection=dict(groups=[],stocks=[]),quality=dict(candidate_confirmed=False))

    def test_append_only_and_shared_market_without_db(self):
        first=self.record();result=cal.persist(first,root=self.root,market=market());path=Path(result['path']);original=path.read_bytes()
        first['status']='변경 시도';self.assertEqual(cal.persist(first,root=self.root,market=market())['status'],'preserved')
        self.assertEqual(path.read_bytes(),original)
        cal.persist(self.record('2026-11-05T09:45:00+09:00'),root=self.root,market=market())
        self.assertEqual(len(cal.records(self.root)),2)
        self.assertEqual(len(list(path.parent.glob('market-*.json'))),1)
        self.assertFalse(list(self.root.rglob('*.db')))

    def test_market_availability_prevents_morning_lookahead(self):
        class DB:
            def execute(self,sql,args=()):
                class Result:
                    def __init__(self,row):self.row=row
                    def fetchone(self):return self.row
                if 'aq_meta' in sql:return Result((json.dumps(dict(created_at='2026-11-05T03:39:16+00:00')),))
                if 'FROM adr20' in sql:return Result(('2026-11-04','68.52','provisional'))
                return Result((504,240))
            def close(self):pass
        with patch.object(cal,'read',return_value={}),patch.object(Path,'is_file',return_value=True),patch.object(cal.sqlite3,'connect',return_value=DB()):
            early=cal.market_context('2026-11-05T09:50:00+09:00')
            late=cal.market_context('2026-11-05T14:30:00+09:00')
        self.assertEqual(early['common']['adr20'],{})
        self.assertEqual(late['common']['adr20']['KOSPI']['value'],'68.52')
        self.assertEqual(late['state'],'판단 자료 부족')

    def test_market_simple_rules_and_future_observation_exclusion(self):
        self.assertEqual(cal.classify_market(dict(domestic_current=dict(KOSPI=dict(change_pct=2),KOSDAQ=dict(change_pct=1))))[0],'강세장 / 불장')
        self.assertEqual(cal.classify_market(dict(domestic_current=dict(KOSPI=dict(change_pct=-4),KOSDAQ=dict(change_pct=-2))))[0],'급락장 / 특이장')
        with patch.object(cal,'read',return_value={}),patch.object(Path,'is_file',return_value=False):
            r=cal.market_context('2026-11-05T09:50:00+09:00',observations=dict(domestic_current=dict(KOSPI=dict(level=999,change_pct=4,observed_at='2026-11-05T09:49:00+09:00',available_at='2026-11-05T10:00:00+09:00'))))
        self.assertIsNone(r['observations']['domestic_current']['KOSPI'])

    def test_frozen_news_calendar_payload_and_script_hash(self):
        group=dict(name='자동차',rank=1,rising_count=2,turnover_eok='1359',leaders=[dict(stock_code='005380',name='현대차',raw_change='5.29')],representative_metrics={})
        view=dict(reference_at='2026-11-05T09:50:00+09:00',analysis=dict(sectors=[group]))
        frame='<article class="sector-rank-card" data-sector-rank="1"><p class="news-comment">공통 특징주 재료 없음</p></article><ol><li class="representative-line"><span class="representative-name">현대차</span></li><li class="company-news"><details class="news-toggle news-comment"><summary>당시 고정 코멘트</summary><ul><li><a href="https://example.com/original">당시 기사</a> — 매체 / 2026-11-05T09:30:00+09:00</li></ul></details></li></ol>'
        result=cal.capture_am_view(view,root=self.root,frame=frame,market=market())
        record=cal.read(result['path']);self.assertEqual(record['selection']['stocks'][0]['news']['summary'],'당시 고정 코멘트')
        with patch('kiwoom_collector.c16_news.cached_summary',side_effect=AssertionError('no reanalysis')),patch('kiwoom_collector.c16_ui.display_articles',side_effect=AssertionError('no article reselection')):
            page,digest=cal.calendar_page(root=self.root,output=self.root)
        script=re.search(r'<script>(.*?)</script>',page,re.S)[1]
        self.assertEqual(digest,base64.b64encode(hashlib.sha256(script.encode()).digest()).decode())
        entries=json.loads(script.split('const RECORDS=',1)[1].split(';\nconst $=',1)[0])
        self.assertTrue(entries[0]['html'].startswith('<!doctype html>'))
        self.assertIn('당시 고정 코멘트',entries[0]['html'])
        self.assertIn('저장된 기록 없음',script);self.assertIn('shift(-1)',script);self.assertIn('shift(1)',script)
        self.assertIn('https://example.com/original',entries[0]['html'])

    def test_calendar_frame_visible_and_repair_existing_style(self):
        html='<head><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'none\';"><style id="calendar-style">old hidden frame rule</style></head><main><label for="session-pm">오후 종가매매</label></main>'
        page=cal.attach_calendar(html,root=self.root,output=self.root)
        self.assertIn('#panel-calendar>.session-frame{display:block}',page)
        self.assertNotIn('old hidden frame rule',page)
        self.assertEqual(page.count('id="calendar-style"'),1)
        self.assertEqual(cal.attach_calendar(page,root=self.root,output=self.root).count('id="panel-calendar"'),1)

    def test_pm_snapshot_keeps_reference_status_and_quality(self):
        data=dict(analysis_at='2026-11-05T15:00:00+09:00',confirmed_candidates=[],reference=dict(sectors=[],candidates=[dict(stock_code='005380',stock_name='현대차',reference_only=True,change_percent_raw='2',turnover_raw='100000',market_cap_won_reference=None,sectors=[],qualification=dict(final_review_allowed=False))]))
        with patch('kiwoom_collector.c16_news.cached_summary',return_value=dict(companies={})):
            result=cal.capture_pm(data,root=self.root,market=market())
        record=cal.read(result['path']);self.assertEqual(record['origin'],'posthoc_reference')
        self.assertEqual(record['selection']['final_eligible_count'],0)
        self.assertFalse(record['selection']['execution_allowed'])
        html=cal.render_record(record,self.root)
        self.assertIn('시총 미확인',html)
        self.assertNotIn('TOP20 미관측',html);self.assertNotIn('test_mode',html)
