"""Offline official-protocol fixtures: never contact Kiwoom."""
from contextlib import closing
from datetime import datetime
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from kiwoom_collector.conditions import (ConditionClient, TARGETS, parse_definitions, map_targets,
    normalize_code, fetch_conditions, condition_report, migrate_conditions)
from kiwoom_collector.config import Credentials
from kiwoom_collector.client import KST, Snapshot
from kiwoom_collector.storage import Store
from kiwoom_collector.sectors import SectorManager
from kiwoom_collector.errors import KiwoomError, ResponseValidationError
from kiwoom_collector.__main__ import main


def listing():
    return {'trnm': 'CNSRLST', 'return_code': 0,
            'data': [[str(n), name] for n, name in zip((7, 12, 29), TARGETS.values())]}


def page(seq, codes=(), **extra):
    return {'trnm': 'CNSRREQ', 'seq': seq, 'return_code': 0, 'cont_yn': 'N', 'next_key': '',
            'data': [{'9001': code, '302': '삼성전자' if code == 'A005930' else '하이닉스', '10': '000021850'} for code in codes], **extra}


class Socket:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.sent = []
        self.closed = False
    def send(self, data): self.sent.append(json.loads(data))
    def recv(self):
        value = next(self.frames)
        if isinstance(value, BaseException): raise value
        return json.dumps(value)
    def settimeout(self, timeout): pass
    def close(self): self.closed = True


def client(frames, environment='mock'):
    socket = Socket(frames)
    auth = Mock()
    auth.authenticate.return_value.value = 'test-access-token'
    connect = Mock(return_value=socket)
    result = ConditionClient(Credentials('test-app-key', 'test-secret-key', environment),
                             connector=connect, auth=auth, sleep=lambda _: None)
    result.open()
    return result, socket, connect


class ProtocolTests(unittest.TestCase):
    def test_mapping_uses_returned_seq_and_exact_names(self):
        rows = parse_definitions(listing()['data'])
        self.assertEqual(map_targets(rows)['sector_seed']['seq'], '7')
        self.assertEqual(map_targets(rows)['independent_high']['seq'], '29')
        for data in (listing()['data'][:-1], listing()['data'] + [['80', '주도주종베']]):
            with self.assertRaises(ResponseValidationError): map_targets(parse_definitions(data))
        data = listing()['data']; data[0][1] += ' '
        with self.assertRaises(ResponseValidationError): map_targets(parse_definitions(data))

    def test_definition_shapes_and_duplicate_seq(self):
        self.assertEqual(parse_definitions([{'seq': '7 ', 'name': '이름'}])[0]['raw_seq'], '7 ')
        for data in (None, [['1']], [['1','a'],['1 ','b']], [[1,'a']]):
            with self.assertRaises(ResponseValidationError): parse_definitions(data)

    def test_code_namespace_and_zeroes(self):
        self.assertEqual(normalize_code('A005930'), '005930')
        self.assertEqual(normalize_code('Q005930'), 'Q005930')
        self.assertEqual(normalize_code('J005930'), 'J005930')
        for code in ('005930', 'A5930', 5930, 'B005930', 'A００５９３０'):
            with self.assertRaises(ResponseValidationError): normalize_code(code)

    def test_login_ping_listing_and_closure(self):
        c, socket, connect = client([{'trnm':'PING','extra':'keep'}, {'trnm':'LOGIN','return_code':0}, listing()])
        try:
            self.assertEqual(len(c.definitions()), 3)
            self.assertEqual(socket.sent[1], {'trnm':'PING','extra':'keep'})
            self.assertEqual(socket.sent[2], {'trnm':'CNSRLST'})
            self.assertIn('mockapi.kiwoom.com:10000', connect.call_args.args[0])
        finally: c.close()
        self.assertTrue(socket.closed)
        c.auth.close.assert_called_once()

    def test_general_pagination_and_padded_seq(self):
        c, socket, _ = client([{'trnm':'LOGIN','return_code':0},
            page('7 ', ['A005930'], cont_yn='Y', next_key='next'), page('7', ['A000660'])])
        try:
            self.assertEqual([s['stock_code'] for s in c.search('7')], ['005930','000660'])
            self.assertEqual(socket.sent[-1], {'trnm':'CNSRREQ','seq':'7','search_type':'0','stex_tp':'K','cont_yn':'Y','next_key':'next'})
            self.assertEqual(len(c.pages), 2)
        finally: c.close()

    def test_bad_responses_are_not_empty_results(self):
        cases = [page('99'), page('7', data=None), page('7', data=[{'9001':'005930'}]),
                 page('7', data=[{'9001':'A005930','10':42}]), page('7', cont_yn='Y', next_key=''),
                 page('7', cont_yn=None), {'trnm':'OTHER'}]
        for response in cases:
            with self.subTest(response=response):
                c, _, _ = client([{'trnm':'LOGIN','return_code':0}, response])
                try:
                    with self.assertRaises(KiwoomError): c.search('7')
                finally: c.close()

    def test_repeated_continuation_fails(self):
        p = page('7', ['A005930'], cont_yn='Y', next_key='repeat')
        c, _, _ = client([{'trnm':'LOGIN','return_code':0}, p, p])
        try:
            with self.assertRaisesRegex(KiwoomError, '반복'): c.search('7')
        finally: c.close()

    def test_mock_unsupported_does_not_expose_message_or_switch_server(self):
        c, socket, connect = client([{'trnm':'LOGIN','return_code':0},
            {'trnm':'CNSRREQ','return_code':8104,'return_msg':'test-access-token test-secret-key'}])
        try:
            with self.assertRaisesRegex(KiwoomError, '모의투자 미지원') as caught: c.search('7')
            self.assertNotIn('test-access-token', str(caught.exception))
            self.assertEqual(connect.call_count, 1)
            self.assertNotIn('return_msg', json.dumps(c.pages))
        finally: c.close()

    def test_timeout_and_rate_fail_without_retry(self):
        for response in (TimeoutError('test-access-token'), {'trnm':'CNSRREQ','return_code':1700}):
            c, socket, _ = client([{'trnm':'LOGIN','return_code':0}, response])
            try:
                with self.assertRaises(KiwoomError) as caught: c.search('7')
                self.assertNotIn('test-access-token', str(caught.exception))
                self.assertEqual(len(socket.sent), 2)
            finally: c.close()

    def test_real_url_and_login_failure(self):
        c, _, connect = client([{'trnm':'LOGIN','return_code':0}], 'real')
        c.close()
        self.assertIn('wss://api.kiwoom.com:10000', connect.call_args.args[0])
        c = ConditionClient(Credentials('k','s'), connector=Mock(return_value=Socket([{'trnm':'LOGIN','return_code':8005}])), auth=Mock())
        c.auth.authenticate.return_value.value = 'token'
        try:
            with self.assertRaises(KiwoomError): c.open()
        finally: c.close()


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'stock1.db'
    def tearDown(self): self.temp.cleanup()

    def fetch(self, responses):
        c, socket, _ = client([{'trnm':'LOGIN','return_code':0}, listing(), *responses])
        try: return fetch_conditions(self.path, c)
        finally: c.close()

    def query(self, sql):
        with closing(sqlite3.connect(self.path)) as db: return db.execute(sql).fetchall()

    def test_batch_dedup_tags_union_raw_and_readonly_analysis(self):
        with closing(Store(self.path)) as store:
            identifier = store.begin(datetime.now(KST), 'mock', '1')
            store.finish(identifier, Snapshot(datetime.now(KST), [{'bigd_rank':'1','stk_cd':'005930','stk_nm':'삼성전자'}]))
            with store.db:
                store.db.execute("UPDATE stocks SET stock_name='기존 이름' WHERE stock_code='005930'")
            manager = SectorManager(store)
            manager.add('반도체'); manager.add('대형주')
            manager.tag('005930','반도체'); manager.tag('005930','대형주')
        batch, failed = self.fetch([page('7', ['A005930','A005930','A000660']), page('12',['A005930']), page('29',[])])
        self.assertFalse(failed)
        before = self.path.read_bytes()
        report = condition_report(self.path, batch_id=batch)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(report['complete_batch'])
        self.assertEqual(report['unique_stock_count'], 2)
        self.assertEqual(report['cross_condition_duplicate_stock_count'], 1)
        self.assertEqual(report['unclassified_stocks'][0]['stock_code'], '000660')
        self.assertEqual(report['conditions'][0]['duplicate_count'], 1)
        self.assertEqual(report['conditions'][2]['status'], 'empty')
        self.assertEqual(len(report['conditions'][0]['sectors']), 2)
        self.assertEqual(report['ranking_intersections'][0]['stocks'][0]['stk_cd'], '005930')
        self.assertEqual(self.query('SELECT COUNT(*) FROM ranking_records')[0][0], 1)
        self.assertEqual(self.query('SELECT COUNT(*) FROM stock_sector_events')[0][0], 2)
        self.assertEqual(self.query("SELECT stock_name FROM stocks WHERE stock_code='005930'")[0][0], '기존 이름')
        raw = json.loads(self.query('SELECT response_json FROM condition_runs ORDER BY rowid')[0][0])
        self.assertEqual(len(raw[0]['data']), 3)  # Duplicate raw rows preserved.
        self.assertEqual(raw[0]['data'][0]['9001'], 'A005930')
        self.assertEqual(self.query("SELECT stock_code FROM stocks WHERE stock_code='000660'")[0][0], '000660')
        self.assertFalse(condition_report(self.path, 'real')['complete_batch'])

    def test_failure_retains_attempts_no_partial_members(self):
        batch, failed = self.fetch([page('7',['A005930'],cont_yn='Y',next_key='next'),
            {'trnm':'CNSRREQ','seq':'7','return_code':8104,'return_msg':'test-secret-key'}])
        self.assertTrue(failed)
        report = condition_report(self.path, batch_id=batch)
        self.assertFalse(report['complete_batch'])
        self.assertEqual(report['unique_stock_count'], 0)
        self.assertEqual([r['status'] for r in report['conditions']], ['error'] * 3)
        self.assertIsNone(report['conditions'][0]['members'])
        self.assertIn('8104', report['conditions'][0]['error'])
        self.assertEqual(self.query('SELECT COUNT(*) FROM condition_members')[0][0], 0)
        self.assertNotIn('test-secret-key', str(self.query('SELECT response_json,error FROM condition_runs')))

    def test_missing_mapping_does_not_search_or_create_db(self):
        c, socket, _ = client([{'trnm':'LOGIN','return_code':0}, {**listing(), 'data':[]}])
        try:
            with self.assertRaises(ResponseValidationError): fetch_conditions(self.path,c)
            self.assertEqual(len(socket.sent), 2)
            self.assertFalse(self.path.exists())
        finally: c.close()

    def test_repeated_fetch_keeps_history_and_definitions(self):
        for _ in range(2): self.fetch([page('7'),page('12'),page('29')])
        self.assertEqual(self.query('SELECT COUNT(*) FROM condition_definitions')[0][0], 3)
        self.assertEqual(self.query('SELECT COUNT(*) FROM condition_runs')[0][0], 6)
        with closing(Store(self.path)) as store:
            migrate_conditions(store.db); migrate_conditions(store.db)
            with self.assertRaises(sqlite3.IntegrityError):
                with store.db: store.db.execute("INSERT INTO condition_members VALUES('missing','005930','A005930',NULL,'{}')")

    def test_redaction_and_unknown_fields_excluded(self):
        p = page('7',['A005930'],token='test-access-token',return_msg='test-secret-key')
        p['data'][0].update({'302':'test-app-key name','token':'test-access-token'})
        self.fetch([p,page('12'),page('29')])
        persisted = str(self.query('SELECT response_json FROM condition_runs')) + str(self.query('SELECT raw_json,stock_name FROM condition_members'))
        for secret in ('test-app-key','test-secret-key','test-access-token'): self.assertNotIn(secret,persisted)
        self.assertIn('[REDACTED]',persisted)

    def test_db_failure_rolls_back_members_and_master(self):
        original = __import__('kiwoom_collector.conditions',fromlist=['upsert_stock']).upsert_stock
        def fail(db, code, name):
            original(db,code,name)
            raise sqlite3.OperationalError('test-secret-key')
        with patch('kiwoom_collector.conditions.upsert_stock',side_effect=fail):
            batch, failed = self.fetch([page('7',['A005930'])])
        self.assertTrue(failed)
        self.assertEqual(self.query('SELECT COUNT(*) FROM condition_members')[0][0],0)
        self.assertEqual(self.query('SELECT COUNT(*) FROM stocks')[0][0],0)
        self.assertIn('DB 저장 실패',condition_report(self.path,batch_id=batch)['conditions'][0]['error'])

    def test_interrupt_marks_pending_runs_and_closes_socket(self):
        c, socket, _ = client([{'trnm':'LOGIN','return_code':0},listing(),KeyboardInterrupt()])
        try:
            with self.assertRaises(KeyboardInterrupt): fetch_conditions(self.path,c)
        finally: c.close()
        self.assertTrue(socket.closed)
        self.assertEqual(self.query("SELECT COUNT(*) FROM condition_runs WHERE status='interrupted'")[0][0],3)

    def test_missing_analysis_does_not_create_file_or_connect(self):
        with patch('kiwoom_collector.conditions.ConditionClient.open') as connect, patch('sys.stderr',new_callable=io.StringIO):
            self.assertEqual(main(['condition','report','--db',str(self.path)]),1)
            connect.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_report_ignores_test_ranking_and_failure_is_not_zero(self):
        with closing(Store(self.path)) as store:
            at = datetime.now(KST)
            run = store.begin(at,'mock','1',test_mode=True)
            store.finish(run,Snapshot(at,[{'bigd_rank':'1','stk_cd':'005930','stk_nm':'삼성전자'}]))
        self.fetch([page('7',['A005930']),page('12'),page('29')])
        report = condition_report(self.path)
        self.assertEqual(report['ranking_intersections'][0]['status'],'missing')
        self.assertIsNone(report['ranking_intersections'][0]['stocks'])

    def test_latest_failed_ranking_not_replaced_by_old_success(self):
        from datetime import timedelta
        with closing(Store(self.path)) as store:
            at = datetime.now(KST)
            old = store.begin(at-timedelta(minutes=2),'mock','1')
            store.finish(old,Snapshot(at,[{'bigd_rank':'1','stk_cd':'005930','stk_nm':'삼성전자'}]))
            bad = store.begin(at-timedelta(minutes=1),'mock','1')
            store.fail(bad,'network')
        self.fetch([page('7',['A005930']),page('12'),page('29')])
        entry = condition_report(self.path)['ranking_intersections'][0]
        self.assertEqual(entry['status'],'ranking_error')
        self.assertIsNone(entry['stocks'])

    def test_other_environment_future_and_prior_day_are_not_joined(self):
        from datetime import timedelta
        with closing(Store(self.path)) as store:
            at = datetime.now(KST)
            for when, env in ((at-timedelta(minutes=2),'real'),(at+timedelta(hours=1),'mock'),(at-timedelta(days=1),'mock')):
                run = store.begin(when,env,'1')
                store.finish(run,Snapshot(when,[{'bigd_rank':'1','stk_cd':'005930','stk_nm':'삼성전자'}]))
        self.fetch([page('7',['A005930']),page('12'),page('29')])
        self.assertEqual(condition_report(self.path)['ranking_intersections'][0]['status'],'missing')

    def test_partial_batch_counts_only_completed_conditions(self):
        self.fetch([page('7',['A005930']),{'trnm':'CNSRREQ','seq':'12','return_code':1700}])
        report = condition_report(self.path)
        self.assertFalse(report['complete_batch'])
        self.assertEqual(report['observed_aliases'],['sector_seed'])
        self.assertEqual(report['unique_stock_count'],1)
        self.assertIsNone(report['conditions'][1]['members'])

    def test_cli_fetch_and_report_are_explicit(self):
        credentials = Credentials('test-app-key','test-secret-key')
        socket = Socket([{'trnm':'LOGIN','return_code':0},listing(),page('7'),page('12'),page('29')])
        with patch('kiwoom_collector.__main__.load_credentials',return_value=credentials), \
             patch('kiwoom_collector.conditions.KiwoomClient') as auth, \
             patch('websocket.create_connection',return_value=socket), patch('kiwoom_collector.conditions.time.sleep'), \
             patch('sys.stdout',new_callable=io.StringIO) as output:
            auth.return_value.authenticate.return_value.value = 'test-access-token'
            self.assertEqual(main(['condition','fetch','--db',str(self.path)]),0)
            self.assertNotIn('test-access-token',output.getvalue())
        with patch('kiwoom_collector.__main__.load_credentials',side_effect=AssertionError('credentials should not load')), patch('sys.stdout',new_callable=io.StringIO):
            self.assertEqual(main(['condition','report','--db',str(self.path)]),0)
        self.assertTrue(socket.closed)


if __name__ == '__main__': unittest.main()
