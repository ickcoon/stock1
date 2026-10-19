from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
from unittest import TestCase
from unittest.mock import Mock, patch

from kiwoom_collector.client import KST, Snapshot, KiwoomError
from kiwoom_collector.schedule import SLOTS, next_slot, slot_at, collect_schedule, load_holidays
from kiwoom_collector.storage import Store
from kiwoom_collector.sectors import SectorManager
from kiwoom_collector.timeline import sector_timeline
from kiwoom_collector.__main__ import main

DAY = date(2026, 10, 19)


class ScheduleTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'stock1.db'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.client = Mock()
        self.client.fetch_once.return_value = Snapshot(slot_at(DAY, '09:20'), [{'bigd_rank': '1', 'stk_cd': '005930', 'stk_nm': '삼성전자'}])

    def test_corrected_ten_slots_and_no_startup_backfill(self):
        self.assertEqual(SLOTS, ('09:20','09:30','09:40','09:50','10:00','11:00','12:00','13:00','14:00','15:00'))
        self.assertEqual(next_slot(slot_at(DAY, '09:20')), slot_at(DAY, '09:20'))
        self.assertEqual(next_slot(slot_at(DAY, '09:20') + timedelta(seconds=1)), slot_at(DAY, '09:30'))
        self.assertEqual(next_slot(slot_at(DAY, '09:40') + timedelta(seconds=1)), slot_at(DAY, '09:50'))
        self.assertEqual(next_slot(slot_at(DAY, '15:00') + timedelta(seconds=1)), slot_at(date(2026,10,20), '09:20'))
        self.assertEqual(next_slot(slot_at(DAY, '09:20').astimezone(timezone.utc)), slot_at(DAY, '09:20'))

    def test_weekend_and_user_verified_holidays(self):
        self.assertEqual(next_slot(slot_at(date(2026,10,23),'15:00') + timedelta(seconds=1)), slot_at(date(2026,10,26),'09:20'))
        self.assertEqual(next_slot(slot_at(DAY,'09:20'), {DAY}), slot_at(date(2026,10,20),'09:20'))
        path = Path(self.temp.name) / 'holidays.txt'
        path.write_text('# 사용자 확인 날짜\n2026-10-19 # 예시\n', encoding='utf-8-sig')
        self.assertEqual(load_holidays(path), {DAY})
        path.write_text('not-a-date', encoding='utf-8')
        with self.assertRaises(ValueError):
            load_holidays(path)

    def test_one_day_exactly_ten_calls_and_restart_no_duplicates(self):
        clock = [slot_at(DAY, '09:20')]
        def sleep(seconds):
            clock[0] += timedelta(seconds=seconds)
            if clock[0] > slot_at(DAY, '15:00'):
                raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            collect_schedule(self.client, self.store, 'mock', now=lambda: clock[0], sleep=sleep)
        self.assertEqual(self.client.fetch_once.call_count, 10)
        self.assertEqual(self.store.status()['attempts'], 10)
        # Restart exactly at an already attempted slot: no second request, even with a new process.
        clock[0] = slot_at(DAY, '09:20')
        with self.assertRaises(KeyboardInterrupt):
            collect_schedule(self.client, self.store, 'mock', now=lambda: clock[0], sleep=Mock(side_effect=KeyboardInterrupt()))
        self.assertEqual(self.client.fetch_once.call_count, 10)

    def test_late_wakeup_skips_missed_slots_without_burst(self):
        clock = [slot_at(DAY, '09:20') - timedelta(seconds=1)]
        sleeps = [0]
        def sleep(seconds):
            sleeps[0] += 1
            if sleeps[0] == 1:
                clock[0] = slot_at(DAY, '10:00') + timedelta(seconds=10)
            else:
                raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            collect_schedule(self.client, self.store, 'mock', now=lambda: clock[0], sleep=sleep)
        self.client.fetch_once.assert_not_called()
        self.assertEqual(self.store.status()['attempts'], 0)

    def test_actual_start_distinct_and_unique_slot_across_connections(self):
        planned = slot_at(DAY, '09:20')
        clock = [planned - timedelta(seconds=1)]
        count = [0]
        def sleep(seconds):
            count[0] += 1
            if count[0] == 1:
                clock[0] = planned + timedelta(seconds=2)
            else:
                raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            collect_schedule(self.client, self.store, 'mock', now=lambda: clock[0], sleep=sleep)
        row = self.store.db.execute('SELECT * FROM collections').fetchone()
        self.assertEqual(row['scheduled_at'], planned.isoformat())
        self.assertEqual(row['collected_at'], clock[0].isoformat())
        with closing(Store(self.path)) as other:
            self.assertIsNone(other.begin(planned + timedelta(seconds=3), 'mock','1',scheduled_at=planned))

    def test_failed_slot_not_retried_on_restart(self):
        self.client.fetch_once.side_effect = KiwoomError('simulated API failure')
        for _ in range(2):
            with self.assertRaises(KeyboardInterrupt):
                collect_schedule(self.client, self.store,'mock',now=lambda: slot_at(DAY,'09:20'),sleep=Mock(side_effect=KeyboardInterrupt()))
        self.assertEqual(self.client.fetch_once.call_count, 1)
        self.assertEqual(self.store.status()['errors'], 1)

    def test_schedule_cli_mutual_exclusions(self):
        for args in (['collect','--schedule','--interval','60'], ['collect','--schedule','--test-now'], ['once','--schedule']):
            with patch('sys.stderr'), self.assertRaises(SystemExit) as exc:
                main(args)
            self.assertEqual(exc.exception.code, 2)


class TimelineTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'stock1.db'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.manager = SectorManager(self.store)

    def record(self, slot, codes, environment='mock', test_mode=False, scheduled=True):
        at = slot_at(DAY, slot)
        identifier = self.store.begin(at,environment,'1',test_mode=test_mode,scheduled_at=at if scheduled else None)
        rows = [{'bigd_rank': str(rank), 'stk_cd': code, 'stk_nm': code+' 종목'} for rank,code in codes]
        self.store.finish(identifier, Snapshot(at, rows))
        return identifier

    def test_baseline_afternoon_replacement_concentration_and_review(self):
        self.record('09:30', [(1,'005930'),(4,'000660'),(8,'035420')])
        self.record('09:40', [(2,'005930'),(3,'000660'),(9,'035420')])
        self.record('13:00', [(1,'005930'),(2,'035420'),(3,'035720')])
        self.record('14:00', [(1,'005930'),(2,'035420'),(3,'035720')])
        self.record('15:00', [(1,'035420'),(2,'035720')])
        for name in ('반도체','인터넷','하드웨어'):
            self.manager.add(name)
        for code in ('005930','000660'):
            self.manager.tag(code,'반도체')
        self.manager.tag('005930','하드웨어')
        for code in ('035420','035720'):
            self.manager.tag(code,'인터넷')
        before = list(self.store.db.iterdump())
        result = sector_timeline(self.path, DAY.isoformat())
        slots = {slot['slot']: slot for slot in result['slots']}
        morning = slots['09:40']
        self.assertEqual(morning['review_candidates'][0]['sector_name'],'반도체')
        ranks = morning['versus_previous']['rank_changes']
        self.assertEqual(next(r['improvement'] for r in ranks if r['stock_code']=='000660'),1)
        afternoon = slots['13:00']
        self.assertFalse(afternoon['versus_previous']['available'])  # 12:00 is missing, not silently 09:40.
        comparison = afternoon['versus_0940']
        self.assertAlmostEqual(comparison['retention_percent'], 200/3)
        self.assertEqual(comparison['entered'][0]['stock_code'], '035720')
        self.assertEqual(comparison['exited'][0]['stock_code'], '000660')
        self.assertEqual(afternoon['new_since_0940_clusters'][0]['sector_name'],'인터넷')
        self.assertEqual(afternoon['afternoon_new_clusters'],[])
        self.assertFalse(afternoon['afternoon_new_clusters_available'])
        self.assertEqual(afternoon['baseline_assessment'][0]['current_count'],1)
        internet = next(s for s in afternoon['sectors'] if s['sector_name']=='인터넷')
        self.assertEqual(internet['top5_count'],2)
        self.assertEqual(internet['top5_ratio_percent'],40)
        self.assertEqual(slots['15:00']['review_stocks'][0]['stock_code'],'035420')
        self.assertEqual(len(slots['15:00']['review_stocks']),2)
        self.assertEqual(list(self.store.db.iterdump()), before)

    def test_missing_baseline_not_treated_as_zero_and_environment_isolation(self):
        self.record('13:00',[(1,'005930'),(2,'000660')])
        self.record('09:40',[(1,'005930'),(2,'000660')],environment='real')
        self.manager.add('반도체')
        for code in ('005930','000660'):
            self.manager.tag(code,'반도체')
        result = sector_timeline(self.path,DAY.isoformat())
        afternoon = next(s for s in result['slots'] if s['slot']=='13:00')
        self.assertFalse(afternoon['versus_0940']['available'])
        self.assertEqual(afternoon['afternoon_new_clusters'],[])

    def test_legacy_and_test_collections_not_inferred_as_schedule(self):
        self.record('09:40',[(1,'005930')],scheduled=False)
        self.record('13:00',[(1,'005930')],test_mode=True)
        result = sector_timeline(self.path,DAY.isoformat())
        self.assertTrue(all(s['status']=='missing' for s in result['slots']))

    def test_empty_error_slots_are_reported_not_comparable(self):
        self.record('09:40',[])
        at = slot_at(DAY,'13:00')
        identifier = self.store.begin(at,'mock','1',scheduled_at=at)
        self.store.fail(identifier,'simulated failure',stage='api_request')
        result = sector_timeline(self.path,DAY.isoformat())
        slots = {slot['slot']: slot for slot in result['slots']}
        self.assertEqual(slots['09:40']['status'],'empty')
        self.assertEqual(slots['13:00']['status'],'error')
        self.assertFalse(slots['13:00']['versus_0940']['available'])

    def test_read_only_cli_without_auth_migration_or_api(self):
        self.record('09:40',[(1,'005930')])
        before = list(self.store.db.iterdump())
        with patch('kiwoom_collector.__main__.Store',side_effect=AssertionError('no migration')), patch('kiwoom_collector.__main__.load_credentials',side_effect=AssertionError('no keys')), patch('builtins.print'):
            self.assertEqual(main(['sector','timeline','--date',DAY.isoformat(),'--db',str(self.path)]),0)
        self.assertEqual(list(self.store.db.iterdump()),before)

    def test_migration_preserves_existing_history_and_tags(self):
        self.record('09:40',[(1,'005930')],scheduled=False)
        self.manager.add('반도체')
        self.manager.tag('005930','반도체')
        self.store.close()
        # Model the previous release schema in a disposable fixture, including its history.
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DROP INDEX idx_unique_scheduled_collection')
            db.execute('ALTER TABLE collections DROP COLUMN scheduled_at')
        with closing(Store(self.path)) as migrated:
            self.assertEqual(migrated.status()['records'],1)
            self.assertEqual(len(SectorManager(migrated).show('005930')['sectors']),1)
            self.assertIsNone(migrated.db.execute('SELECT scheduled_at FROM collections').fetchone()[0])
            self.assertEqual(migrated.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_afternoon_cluster_transition_differs_from_morning_baseline(self):
        self.record('09:40',[(1,'005930')])
        self.record('12:00',[(1,'035420')])
        self.record('13:00',[(1,'035420'),(2,'035720')])
        self.record('14:00',[(1,'035420'),(2,'035720')])
        self.manager.add('인터넷')
        for code in ('035420','035720'):
            self.manager.tag(code,'인터넷')
        report = sector_timeline(self.path,DAY.isoformat())
        slots = {row['slot']:row for row in report['slots']}
        self.assertEqual(slots['13:00']['afternoon_new_clusters'][0]['sector_name'],'인터넷')
        self.assertTrue(slots['14:00']['new_since_0940_clusters'])
        self.assertEqual(slots['14:00']['afternoon_new_clusters'],[])
