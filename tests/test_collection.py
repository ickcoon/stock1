from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest import TestCase
from unittest.mock import Mock, patch

from kiwoom_collector.client import KST, KiwoomError, Snapshot
from kiwoom_collector.collector import collect, next_open
from kiwoom_collector.storage import Store
from kiwoom_collector.__main__ import main


def at(day=19, hour=9, minute=0, second=0):
    return datetime(2026, 10, day, hour, minute, second, tzinfo=KST)


class StorageTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'data' / 'stock1.db'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)

    def snapshot(self):
        row = {'bigd_rank': '1', 'stk_cd': '039490', 'stk_nm': '키움증권', 'base_comp_chgr': '+0.57'}
        return Snapshot(at(), [row], [{'return_code': 0, 'return_msg': 'do-not-store', 'token': 'do-not-store', 'item_inq_rank': [row]}])

    def test_preserve_original_types_and_history(self):
        for timestamp in (at(), at(minute=5)):
            identifier = self.store.begin(timestamp, 'mock', '1')
            self.store.finish(identifier, self.snapshot())
        state = self.store.status()
        self.assertEqual((state['attempts'], state['records']), (2, 2))
        run, rows = self.store.latest()
        self.assertEqual(run['collected_at'], at(minute=5).isoformat())
        self.assertEqual(rows[0]['rank'], 1)
        raw = self.store.db.execute('SELECT raw_json FROM ranking_records LIMIT 1').fetchone()[0]
        self.assertEqual(json.loads(raw)['bigd_rank'], '1')
        self.assertNotIn('trading_value', raw)
        self.assertNotIn('do-not-store', run['response_json'])
        self.assertEqual(json.loads(run['response_json'])[0]['item_inq_rank'][0]['stk_cd'], '039490')

    def test_duplicate_time_blocked_across_connections(self):
        self.assertIsNotNone(self.store.begin(at(), 'mock', '1'))
        other = Store(self.path)
        try:
            self.assertIsNone(other.begin(at(), 'mock', '1'))
        finally:
            other.close()
        self.assertEqual(self.store.status()['attempts'], 1)

    def test_duplicate_rank_rolls_back_all_rows(self):
        identifier = self.store.begin(at(), 'mock', '1')
        snap = self.snapshot()
        snap.records.append(dict(snap.records[0]))
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.finish(identifier, snap)
        self.assertEqual(self.store.status()['records'], 0)

    def test_duplicate_stock_rolls_back(self):
        identifier = self.store.begin(at(), 'mock', '1')
        snap = self.snapshot()
        snap.records.append(dict(snap.records[0], bigd_rank='2'))
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.finish(identifier, snap)
        self.assertEqual(self.store.status()['records'], 0)

    def test_failure_empty_and_interruption_distinct(self):
        identifier = self.store.begin(at(), 'mock', '1')
        self.store.fail(identifier, 'network failure')
        identifier = self.store.begin(at(minute=5), 'mock', '1')
        self.store.finish(identifier, Snapshot(at(), []))
        identifier = self.store.begin(at(minute=10), 'mock', '1')
        self.store.fail(identifier, 'user interrupted', interrupted=True)
        state = self.store.status()
        self.assertEqual((state['errors'], state['empty'], state['interrupted'], state['records']), (1, 1, 1, 0))
        self.assertEqual(self.store.latest()[0]['status'], 'empty')

    def test_status_latest_need_no_credentials_or_network(self):
        for command in ('status', 'latest'):
            with patch('kiwoom_collector.__main__.load_credentials', side_effect=AssertionError('must not load')), patch('kiwoom_collector.client.requests.Session', side_effect=AssertionError('must not request')), patch('builtins.print'):
                self.assertEqual(main([command, '--db', str(self.path)]), 0)

    def test_wrong_field_type_not_silently_converted(self):
        identifier = self.store.begin(at(), 'mock', '1')
        snap = self.snapshot()
        snap.records[0]['stk_cd'] = 39490
        with self.assertRaises(ValueError):
            self.store.finish(identifier, snap)
        self.assertEqual(self.store.status()['records'], 0)


class SchedulerTests(TestCase):
    def test_session_boundaries_weekend_and_kst(self):
        self.assertEqual(next_open(at(hour=8)), at())
        self.assertEqual(next_open(at()), at())
        self.assertEqual(next_open(at(hour=15, minute=29)), at(hour=15, minute=29))
        self.assertEqual(next_open(at(hour=15, minute=30)), at(day=20))
        self.assertEqual(next_open(at(day=23, hour=16)), at(day=26))
        self.assertEqual(next_open(at(day=24)), at(day=26))
        self.assertEqual(next_open(at().astimezone(__import__('datetime').timezone.utc)), at())

    def run_cycles(self, effects):
        clock = [at()]
        def sleep(seconds):
            clock[0] += timedelta(seconds=seconds)
        client = Mock()
        client.fetch_once.side_effect = effects
        store = Mock()
        store.begin.return_value = 'id'
        with self.assertRaises(KeyboardInterrupt):
            collect(client, store, 'mock', now=lambda: clock[0], sleep=sleep)
        return client, store

    def test_failure_then_next_cycle_success_then_ctrl_c(self):
        snapshot = Snapshot(at(), [{'bigd_rank': '1'}])
        client, store = self.run_cycles([KiwoomError('network failure'), snapshot, KeyboardInterrupt()])
        self.assertEqual(client.fetch_once.call_count, 3)
        times = [call.args[0] for call in store.begin.call_args_list]
        self.assertEqual(times, [at(), at(minute=5), at(minute=10)])
        store.finish.assert_called_once_with('id', snapshot)
        self.assertEqual(store.fail.call_count, 2)
        self.assertTrue(store.fail.call_args.kwargs['interrupted'])

    def test_outside_hours_never_calls_api(self):
        for current in (at(hour=8), at(hour=15, minute=30), at(day=24)):
            client, store = Mock(), Mock()
            with self.assertRaises(KeyboardInterrupt):
                collect(client, store, 'mock', now=lambda: current, sleep=Mock(side_effect=KeyboardInterrupt()))
            client.fetch_once.assert_not_called()
            store.begin.assert_not_called()

    def test_duplicate_begin_skips_network(self):
        client, store = Mock(), Mock()
        store.begin.return_value = None
        with self.assertRaises(KeyboardInterrupt):
            collect(client, store, 'mock', now=lambda: at(), sleep=Mock(side_effect=KeyboardInterrupt()))
        client.fetch_once.assert_not_called()

    def test_interval_validation(self):
        with self.assertRaises(ValueError):
            collect(Mock(), Mock(), 'mock', interval_seconds=0)

class EndToEndTests(TestCase):
    def test_mock_http_to_real_sqlite_and_cli(self):
        from kiwoom_collector.client import KiwoomClient
        from kiwoom_collector.config import Credentials
        from clock_helpers import request_clock
        with tempfile.TemporaryDirectory() as directory, patch('kiwoom_collector.client.requests.Session') as session_factory, request_clock():
            store = Store(Path(directory) / 'stock1.db')
            client = KiwoomClient(Credentials('test-key', 'test-secret'))
            auth = Mock(status_code=200, headers={})
            auth.json.return_value = {'token': 'test-only-token', 'token_type': 'bearer',
                                      'expires_dt': '20990101000000', 'return_code': 0}
            page = Mock(status_code=200, headers={})
            page.json.return_value = {'return_code': 0, 'item_inq_rank': [
                {'bigd_rank': str(rank), 'stk_cd': str(rank).zfill(6), 'stk_nm': '테스트'}
                for rank in range(1, 21)]}
            session_factory.return_value.post.side_effect = [auth, page]
            clock = [at()]
            def sleep(seconds):
                raise KeyboardInterrupt()
            try:
                with self.assertRaises(KeyboardInterrupt):
                    collect(client, store, 'mock', now=lambda: clock[0], sleep=sleep)
                self.assertEqual(store.status()['records'], 20)
                self.assertEqual(store.latest()[1][-1]['rank'], 20)
                store.close()
                for command in ('status', 'latest'):
                    with patch('builtins.print') as output:
                        self.assertEqual(main([command, '--db', str(Path(directory) / 'stock1.db')]), 0)
                        self.assertIn('20', str(output.call_args_list))
            finally:
                client.close()
                store.close()



class TimingAndRecoveryTests(TestCase):
    def test_custom_interval_and_no_catchup_burst(self):
        clock = [at()]
        client, store = Mock(), Mock()
        store.begin.return_value = 'id'
        def fetch(qry_tp):
            if client.fetch_once.call_count == 1:
                clock[0] += timedelta(seconds=180)
                return Snapshot(clock[0], [])
            raise KeyboardInterrupt()
        client.fetch_once.side_effect = fetch
        def sleep(seconds):
            clock[0] += timedelta(seconds=seconds)
        with self.assertRaises(KeyboardInterrupt):
            collect(client, store, 'mock', interval_seconds=60, now=lambda: clock[0], sleep=sleep)
        self.assertEqual([call.args[0] for call in store.begin.call_args_list], [at(), at(minute=4)])

    def test_integrity_failure_recorded_next_cycle_attempted(self):
        clock = [at()]
        client, store = Mock(), Mock()
        store.begin.return_value = 'id'
        client.fetch_once.side_effect = [Snapshot(at(), []), KeyboardInterrupt()]
        store.finish.side_effect = sqlite3.IntegrityError('private detail')
        def sleep(seconds):
            clock[0] += timedelta(seconds=seconds)
        with self.assertRaises(KeyboardInterrupt):
            collect(client, store, 'mock', now=lambda: clock[0], sleep=sleep)
        self.assertEqual(client.fetch_once.call_count, 2)
        self.assertNotIn('private detail', str(store.fail.call_args_list))

    def test_database_checks_and_foreign_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'stock1.db')
            try:
                with self.assertRaises(sqlite3.IntegrityError), store.db:
                    store.db.execute("INSERT INTO ranking_records VALUES('missing',1,'000001','test','{}')")
                self.assertEqual(store.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(store.db.execute('PRAGMA foreign_key_check').fetchall(), [])
            finally:
                store.close()
