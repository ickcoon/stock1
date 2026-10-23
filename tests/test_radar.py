"""Radar temporal rules and offline CLI integration against real schema fixtures."""
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_collector.client import KST, Snapshot
from kiwoom_collector.storage import Store
from kiwoom_collector.sectors import SectorManager
from kiwoom_collector.quotes import migrate_quotes, SPEC_VERSION
from kiwoom_collector.radar import assess, radar_report, render_radar, export_radar
from kiwoom_collector.__main__ import main

AT = '2026-10-20T09:40:00+09:00'


def quote(rate='2.5', observed='2026-10-20T09:41:00+09:00', api_date='20261020', api_time='094100'):
    return dict(api_id='ka10095', status='success', request_finished_at=observed,
                spec_version=SPEC_VERSION, issues_json='[]', normalized_json=json.dumps(dict(
                    version=1, change_percent=rate, price_status='unverified_sign_semantics',
                    turnover_won='123000000', turnover_source_unit='million_KRW',
                    api_date_raw=api_date, api_time_raw=api_time)))


class AssessmentTests(unittest.TestCase):
    def test_direction_and_independent_price_warning(self):
        for rate, direction in [('2.5','up'),('-1','down'),('0','flat')]:
            result = assess(quote(rate), AT)
            self.assertTrue(result['eligible'])
            self.assertEqual(result['direction'], direction)
            self.assertEqual(result['candidate'], direction == 'up')
            self.assertIn('가격 부호 미확정', result['warnings'])

    def test_historical_ranking_current_quote_is_excluded(self):
        result = assess(quote(observed='2026-10-22T18:46:00+09:00', api_date='20261022', api_time='184600'), AT)
        self.assertFalse(result['eligible'])
        self.assertIn('시점 불일치·분석 제외', result['warnings'])
        self.assertEqual(result['change_percent'], '2.5')
        self.assertIsNone(result['direction'])
        legacy = quote(observed='2026-10-22T18:46:00+09:00')
        legacy['normalized_json'] = None
        self.assertIn('시점 불일치·분석 제외', assess(legacy, AT)['warnings'])

    def test_skew_boundary_dates_timezones_and_stale_execution(self):
        self.assertTrue(assess(quote(observed='2026-10-20T00:45:00+00:00', api_time='094500'), AT)['eligible'])
        for observed, day, tm in [('2026-10-20T09:45:01+09:00','20261020','094500'),
                                  ('2026-10-20T09:41:00+09:00','20261020','090000'),
                                  ('2026-10-20T09:41:00','20261020','094100'),
                                  ('bad','20261020','094100'),
                                  ('2026-10-20T09:41:00+09:00','20261020','')]:
            self.assertFalse(assess(quote(observed=observed,api_date=day,api_time=tm), AT)['eligible'])
        self.assertFalse(assess(quote(observed='2026-10-21T00:01:00+09:00',api_date='20261021',api_time='000100'), '2026-10-20T23:59:00+09:00')['eligible'])

    def test_missing_unverified_unit_and_nonfinite_are_distinct(self):
        self.assertEqual(assess(None, AT)['warnings'], ['시세 미수집'])
        q=quote()
        q['normalized_json']=None
        result=assess(q, AT)
        self.assertIsNone(result['change_percent'])
        self.assertIsNone(result['turnover_won'])
        self.assertIn('필드 미검증: 등락률', result['warnings'])
        self.assertIn('거래대금 단위/값 미검증', result['warnings'])
        for rate in ('NaN','Infinity',None):
            self.assertFalse(assess(quote(rate), AT)['eligible'])
        q=quote()
        q['spec_version']='unknown'
        self.assertFalse(assess(q, AT)['eligible'])
        q=quote(); q['status']='error'
        self.assertFalse(assess(q, AT)['eligible'])


class RadarIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'stock.db'
        self.output=Path(self.tmp.name)/'radar.html'
        at=datetime.fromisoformat(AT)
        with closing(Store(self.path)) as store:
            self.identifier=store.begin(at,'mock','1')
            store.finish(self.identifier, Snapshot(at,[dict(bigd_rank=str(i),stk_cd=f'{i:06}',stk_nm='<script>alert(1)</script>' if i==1 else f'Stock {i}') for i in range(1,5)]))
            manager=SectorManager(store)
            manager.add("반도체")
            manager.add("전기")
            for i in range(1,5): manager.tag(f'{i:06}','반도체')
            manager.tag('000001','전기')
            with store.db:
                migrate_quotes(store.db)
                store.db.execute("INSERT INTO quote_batches(id,collection_id,environment,spec_version,started_at,status,api_id) VALUES('b',?,'mock',?,?,'success','ka10095')",(self.identifier,SPEC_VERSION,AT))
                for rank, rate in [(1,'2'),(2,'-1'),(3,'0')]:
                    q=quote(rate)
                    store.db.execute('''INSERT INTO quote_observations(batch_id,collection_id,rank,requested_code,api_id,status,request_finished_at,normalized_json)
                        VALUES('b',?,?,?,'ka10095','success',?,?)''',(self.identifier,rank,f'{rank:06}',q['request_finished_at'],q['normalized_json']))

    def test_schema_join_multisector_counts_and_html_escape(self):
        report=radar_report(self.path,'2026-10-20')
        group=next(g for g in report['runs'][0]['sectors'] if g['name']=='반도체')
        self.assertEqual((group['interest_count'],group['up'],group['down'],group['flat'],group['excluded'],group['top5'],group['top10']),(4,1,1,1,1,4,4))
        self.assertEqual(len(report['runs'][0]['sectors']),2)
        html=render_radar(report)
        self.assertNotIn('<script>',html)
        self.assertIn('&lt;script&gt;',html)
        self.assertIn('시세 상승이 확인된 주도주 후보',html)
        self.assertIn('시세 미수집',html)
        self.assertNotIn('https://',html)
        self.assertEqual(radar_report(self.path,'2026-10-20','real')['runs'],[])
        self.assertIn('성공한 순위 수집이 없습니다',render_radar(radar_report(self.path,'2026-10-21')))

    def test_readonly_cli_no_credentials_store_migration_network(self):
        before=self.path.read_bytes()
        with patch('kiwoom_collector.__main__.load_credentials',side_effect=AssertionError('credentials')), \
             patch('kiwoom_collector.__main__.Store',side_effect=AssertionError('Store')), \
             patch('socket.socket.connect',side_effect=AssertionError('network')), patch('builtins.print'):
            self.assertEqual(main(['sector','radar','--date','2026-10-20','--environment','mock','--db',str(self.path),'--output',str(self.output)]),0)
        self.assertTrue(self.output.exists())
        self.assertEqual(before,self.path.read_bytes())
        with self.assertRaises(ValueError): export_radar(self.path,'2026-10-20','mock',self.path)
        with self.assertRaises(ValueError): radar_report(self.path,'invalid')
        with self.assertRaises(SystemExit): main(['sector','radar'])
        with self.assertRaises(SystemExit): main(['sector','radar','--output',str(self.output),'--qry-tp','1'])

    def test_legacy_database_without_quotes_remains_unchanged(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('DROP TABLE quote_observations')
            db.execute('DROP TABLE quote_batches')
            db.commit()
        before=self.path.read_bytes()
        report=radar_report(self.path,'2026-10-20')
        self.assertEqual(report['runs'][0]['stocks'][0]['quote']['warnings'],['시세 미수집'])
        self.assertEqual(before,self.path.read_bytes())

    def test_multiple_attempts_prefer_aligned_and_show_all(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('ALTER TABLE quote_observations ADD COLUMN attempt INTEGER DEFAULT 0')
            # Different API fits the old primary key and exercises duplicate observations.
            q=quote(observed='2026-10-22T18:00:00+09:00',api_date='20261022',api_time='180000')
            db.execute("INSERT INTO quote_batches(id,collection_id,environment,spec_version,started_at,status,api_id) VALUES('c',?,'mock',?,?,'success','ka10007')",(self.identifier,SPEC_VERSION,AT))
            db.execute("INSERT INTO quote_observations(batch_id,collection_id,rank,requested_code,api_id,status,request_finished_at,normalized_json) VALUES('c',?,1,'000001','ka10007','success',?,?)",(self.identifier,q['request_finished_at'],q['normalized_json']))
            db.commit()
        stock=radar_report(self.path,'2026-10-20')['runs'][0]['stocks'][0]
        self.assertTrue(stock['quote']['eligible'])
        self.assertEqual(len(stock['observations']),2)
        self.assertIn('시점 불일치·분석 제외',render_radar(radar_report(self.path,'2026-10-20')))
