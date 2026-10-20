"""Explicit, read-only Kiwoom condition queries. No realtime subscriptions/orders.

Protocol source: official Kiwoom-REST-API spec, commit 953e5dbff123f437ab4d11a78a95191a685eb51f.
"""
from contextlib import closing
from datetime import datetime
import json
import re
import sqlite3
import time
from uuid import uuid4

from .client import KiwoomClient, _code
from .errors import KiwoomError, ResponseValidationError
from .sectors import timestamp, upsert_stock
from .storage import Store
from .summary import read_database, _rows

TARGETS = {'sector_seed': '주도주종베', 'sector_cross': '당일주도주(상따겸용)', 'independent_high': '끼도주'}
FIELDS = ('9001', '302', '10', '25', '11', '12', '13', '16', '17', '18')


def normalize_code(value):
    if not isinstance(value, str) or re.fullmatch(r'[AJQ][0-9]{6}', value) is None:
        raise ResponseValidationError('condition.data.9001: 공식 접두어+6자리 코드 형식 필요 (값 생략)')
    # Preserve instrument namespace: ETN/ELW must not collide with equity codes.
    return value[1:] if value.startswith('A') else value


def parse_definitions(data):
    if not isinstance(data, list):
        raise ResponseValidationError('CNSRLST.data: 목록 필요')
    result, seqs = [], set()
    for row in data:
        if isinstance(row, list) and len(row) == 2:
            seq, name = row
        elif isinstance(row, dict):
            seq, name = row.get('seq'), row.get('name')
        else:
            raise ResponseValidationError('CNSRLST.data: seq/name 행 형식 불일치')
        if not isinstance(seq, str) or not seq.strip() or not isinstance(name, str) or not name:
            raise ResponseValidationError('CNSRLST: seq/name 문자열 필요')
        if seq.strip() in seqs:
            raise ResponseValidationError('CNSRLST: 중복 seq')
        seqs.add(seq.strip())
        result.append({'seq': seq.strip(), 'raw_seq': seq, 'name': name})
    return result


def map_targets(definitions):
    result = {}
    for alias, name in TARGETS.items():
        matches = [row for row in definitions if row['name'] == name]
        if len(matches) != 1:
            raise ResponseValidationError(f'{alias}: 정확한 조건식 이름의 항목이 1개여야 합니다 (발견 {len(matches)}개).')
        result[alias] = matches[0]
    return result


class ConditionClient:
    """One sequential socket, bounded waits, no implicit reconnect/replay."""
    def __init__(self, credentials, *, connector=None, auth=None, clock=time.monotonic, sleep=time.sleep):
        self.credentials = credentials
        self.auth = auth or KiwoomClient(credentials)
        self.connector = connector
        self.clock, self.sleep = clock, sleep
        self.socket = None
        self.last_send = None
        self.pages = []
        self.secrets = [credentials.app_key, credentials.secret_key]

    def scrub(self, value):
        if isinstance(value, str):
            for secret in self.secrets:
                if secret:
                    value = value.replace(secret, '[REDACTED]')
            return value
        if isinstance(value, list):
            return [self.scrub(v) for v in value]
        if isinstance(value, dict):
            return {k: self.scrub(v) for k, v in value.items()}
        return value

    def close(self):
        try:
            if self.socket is not None:
                try:
                    self.socket.close()
                except Exception:
                    pass  # Closing a broken transport must not mask a safe primary error.
        finally:
            self.socket = None
            self.auth.close()

    def open(self):
        token = self.auth.authenticate().value
        self.secrets.append(token)
        try:
            if self.connector is None:
                import websocket
                self.connector = websocket.create_connection
            host = 'mockapi.kiwoom.com' if self.credentials.environment == 'mock' else 'api.kiwoom.com'
            # No websocket tracing or arbitrary hosts; TLS verification stays enabled.
            self.socket = self.connector(f'wss://{host}:10000/api/dostk/websocket', timeout=20)
            self.exchange({'trnm': 'LOGIN', 'token': token}, 'LOGIN')
        except (KiwoomError, KeyboardInterrupt):
            raise
        except Exception:
            raise KiwoomError('조건검색 WebSocket 연결 실패: 네트워크/TLS/서버 접근을 확인하세요 (원문 생략).') from None

    def exchange(self, request, expected, seq=None):
        try:
            if self.last_send is not None:
                # Local conservative policy, NOT an official published limit.
                self.sleep(max(0, 1 - (self.clock() - self.last_send)))
            self.socket.send(json.dumps(request, ensure_ascii=False))
            self.last_send = self.clock()
            deadline = self.clock() + 30
            for _ in range(100):
                remaining = deadline - self.clock()
                if remaining <= 0:
                    break
                self.socket.settimeout(min(20, remaining))
                raw = self.socket.recv()
                try:
                    body = json.loads(raw)
                except (ValueError, TypeError):
                    raise ResponseValidationError('조건검색 WebSocket: JSON 응답 형식 불일치') from None
                if not isinstance(body, dict):
                    raise ResponseValidationError('조건검색 WebSocket: 객체 응답 필요')
                if body.get('trnm') == 'PING':
                    self.socket.send(json.dumps(body))
                    continue
                code = _code(body)
                if code not in (None, 0):
                    if expected == 'CNSRREQ':
                        self.pages.append(self.safe_page(body))
                    reason = '모의투자 미지원' if code == 8104 else 'API 오류'
                    raise KiwoomError(f'조건검색 {reason}: return_code={code}; 환경={self.credentials.environment}. 자동 환경 전환/재호출 안 함.')
                if body.get('trnm') != expected:
                    raise ResponseValidationError('조건검색: 예상하지 않은 응답 서비스 (원문 생략)')
                if seq is not None and (not isinstance(body.get('seq'), str) or body['seq'].strip() != seq):
                    raise ResponseValidationError('조건검색: 요청/응답 seq 불일치')
                if expected == 'LOGIN' and code is None:
                    raise ResponseValidationError('LOGIN: 인증 결과코드 누락')
                return body
            raise KiwoomError('조건검색 응답 대기 한도 초과')
        except KiwoomError:
            raise
        except Exception:
            raise KiwoomError('조건검색 WebSocket 송수신 실패/시간 초과 (원문 생략). 다시 명시적으로 실행하세요.') from None

    def definitions(self):
        body = self.exchange({'trnm': 'CNSRLST'}, 'CNSRLST')
        return parse_definitions(body.get('data'))

    def safe_page(self, body):
        # Documented fields only; no return_msg, LOGIN, token, headers, or unknown fields.
        result = {k: body[k] for k in ('trnm', 'seq', 'cont_yn', 'next_key', 'return_code')
                  if isinstance(body.get(k), (str, int)) and not isinstance(body.get(k), bool)}
        if isinstance(body.get('data'), list):
            result['data'] = [{k: row[k] for k in FIELDS if isinstance(row.get(k), str)}
                              for row in body['data'] if isinstance(row, dict)]
        return self.scrub(result)

    def search(self, seq):
        self.pages = []
        rows, keys = [], set()
        continuation = {'cont_yn': 'N', 'next_key': ''}
        for _ in range(100):
            body = self.exchange({'trnm': 'CNSRREQ', 'seq': seq, 'search_type': '0',
                                  'stex_tp': 'K', **continuation}, 'CNSRREQ', seq)
            self.pages.append(self.safe_page(body))
            data = body.get('data')
            if not isinstance(data, list):
                raise ResponseValidationError('CNSRREQ.data: 목록 필요; 누락을 빈 결과로 해석하지 않습니다.')
            for row in data:
                if not isinstance(row, dict):
                    raise ResponseValidationError('CNSRREQ.data: 객체 행 필요')
                code = normalize_code(row.get('9001'))
                if any(k in row and not isinstance(row[k], str) for k in FIELDS):
                    raise ResponseValidationError('CNSRREQ.data: 공식 필드의 문자열 자료형 불일치')
                rows.append({'stock_code': code, 'stock_name': row.get('302'),
                             'raw_code': row['9001'], 'raw': self.scrub({k: row[k] for k in FIELDS if k in row})})
            flag = body.get('cont_yn')
            if flag == 'N':
                return rows
            if flag != 'Y':
                raise ResponseValidationError('CNSRREQ.cont_yn: Y/N 필요; 결과 완전성 확인 불가')
            key = body.get('next_key')
            if not isinstance(key, str) or not key or key in keys:
                raise ResponseValidationError('조건검색 연속조회 키 누락/반복: 부분 결과 저장 안 함')
            keys.add(key)
            continuation = {'cont_yn': 'Y', 'next_key': key}
        raise ResponseValidationError('조건검색 연속조회 100페이지 초과')


def migrate_conditions(db):
    # Additive DDL, one transaction; historical tables and tag history are untouched.
    with db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''CREATE TABLE IF NOT EXISTS condition_definitions (
            id INTEGER PRIMARY KEY, environment TEXT NOT NULL CHECK(environment IN ('mock','real')),
            alias TEXT NOT NULL, name TEXT NOT NULL, seq TEXT NOT NULL, raw_seq TEXT NOT NULL,
            discovered_at TEXT NOT NULL, UNIQUE(environment,alias,name,seq))''')
        db.execute('''CREATE TABLE IF NOT EXISTS condition_runs (
            id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, definition_id INTEGER NOT NULL REFERENCES condition_definitions(id),
            requested_at TEXT NOT NULL, completed_at TEXT, status TEXT NOT NULL
            CHECK(status IN ('running','success','empty','error','interrupted')),
            api_id TEXT NOT NULL DEFAULT 'ka10172', exchange TEXT NOT NULL DEFAULT 'K',
            record_count INTEGER NOT NULL DEFAULT 0, duplicate_count INTEGER NOT NULL DEFAULT 0,
            error TEXT, response_json TEXT, UNIQUE(batch_id,definition_id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS condition_members (
            run_id TEXT NOT NULL REFERENCES condition_runs(id), stock_code TEXT NOT NULL,
            raw_code TEXT NOT NULL, stock_name TEXT, raw_json TEXT NOT NULL,
            PRIMARY KEY(run_id,stock_code))''')
        db.execute('CREATE INDEX IF NOT EXISTS idx_condition_runs_batch ON condition_runs(batch_id)')
        db.execute('CREATE INDEX IF NOT EXISTS idx_condition_members_code ON condition_members(stock_code,run_id)')


def fetch_conditions(path, client):
    """One explicit batch; no existing collector process or scheduling changes."""
    definitions = map_targets(client.definitions())  # Validate ALL names before any searches.
    batch_id = str(uuid4())
    with closing(Store(path)) as store:
        migrate_conditions(store.db)
        db = store.db
        with db:
            for alias, definition in definitions.items():
                db.execute('''INSERT INTO condition_definitions(environment,alias,name,seq,raw_seq,discovered_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(environment,alias,name,seq)
                    DO UPDATE SET raw_seq=excluded.raw_seq,discovered_at=excluded.discovered_at''',
                    (client.credentials.environment, alias, definition['name'], definition['seq'], definition['raw_seq'], timestamp()))
                definition_id = db.execute('SELECT id FROM condition_definitions WHERE environment=? AND alias=? AND name=? AND seq=?',
                                          (client.credentials.environment, alias, definition['name'], definition['seq'])).fetchone()[0]
                db.execute('INSERT INTO condition_runs(id,batch_id,definition_id,requested_at,status) VALUES(?,?,?,?,?)',
                           (str(uuid4()), batch_id, definition_id, timestamp(), 'running'))
        failed = False
        runs = db.execute('''SELECT r.id,d.seq FROM condition_runs r JOIN condition_definitions d ON d.id=r.definition_id
                             WHERE r.batch_id=? ORDER BY r.rowid''', (batch_id,)).fetchall()
        try:
            for run in runs:
                client.pages = []
                try:
                    with db:
                        db.execute('UPDATE condition_runs SET requested_at=? WHERE id=?', (timestamp(), run['id']))
                    rows = client.search(run['seq'])
                    members = {}
                    for row in rows:
                        members.setdefault(row['stock_code'], row)
                    with db:
                        for row in members.values():
                            db.execute('INSERT INTO condition_members VALUES(?,?,?,?,?)',
                                       (run['id'], row['stock_code'], row['raw_code'], client.scrub(row['stock_name']),
                                        json.dumps(row['raw'], ensure_ascii=False)))
                            # Preserve existing master names; retain observed names in this run.
                            if not db.execute('SELECT 1 FROM stocks WHERE stock_code=?', (row['stock_code'],)).fetchone():
                                upsert_stock(db, row['stock_code'], client.scrub(row['stock_name']))
                        db.execute('''UPDATE condition_runs SET status=?,completed_at=?,record_count=?,duplicate_count=?,response_json=? WHERE id=?''',
                                   ('success' if members else 'empty', timestamp(), len(members), len(rows)-len(members),
                                    json.dumps(client.pages, ensure_ascii=False), run['id']))
                except (KiwoomError, sqlite3.Error) as exc:
                    failed = True
                    message = str(exc) if isinstance(exc, KiwoomError) else '조건검색 DB 저장 실패 (상세 값 생략)'
                    with db:
                        db.execute('UPDATE condition_runs SET status=?,completed_at=?,error=?,response_json=? WHERE id=?',
                                   ('error', timestamp(), client.scrub(message), json.dumps(client.pages, ensure_ascii=False), run['id']))
                    # Stop batch on transport/auth/rate/DB failure. Never auto-retry unrelated queries.
                    with db:
                        db.execute("UPDATE condition_runs SET status='error',completed_at=?,error=? WHERE batch_id=? AND status='running'",
                                   (timestamp(), '앞선 검색 실패로 실행하지 않음', batch_id))
                    break
        except KeyboardInterrupt:
            with db:
                db.execute("UPDATE condition_runs SET status='interrupted',completed_at=?,error=? WHERE batch_id=? AND status='running'",
                           (timestamp(), '사용자 중단', batch_id))
            raise
    return batch_id, failed


def condition_report(path, environment='mock', batch_id=None):
    """Consistent read-only snapshot; never replaces latest failures with older successes."""
    with read_database(path) as (db, resolved):
        tables = {r['name'] for r in _rows(db, "SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'condition_definitions', 'condition_runs', 'condition_members'} <= tables:
            raise ValueError('조건검색 저장 이력이 없습니다. condition fetch를 명시적으로 실행하세요.')
        if batch_id is None:
            found = _rows(db, '''SELECT r.batch_id FROM condition_runs r JOIN condition_definitions d ON d.id=r.definition_id
                WHERE d.environment=? ORDER BY r.requested_at DESC,r.rowid DESC LIMIT 1''', (environment,))
            batch_id = found[0]['batch_id'] if found else None
        runs = _rows(db, '''SELECT r.*,d.alias,d.name,d.seq,d.environment FROM condition_runs r
            JOIN condition_definitions d ON d.id=r.definition_id WHERE r.batch_id=? AND d.environment=? ORDER BY r.rowid''', (batch_id, environment))
        output = {'db': str(resolved), 'environment': environment, 'is_mock': environment == 'mock', 'batch_id': batch_id,
                  'conditions': [], 'unique_stock_count': 0, 'cross_condition_duplicate_stock_count': 0,
                  'unclassified_stocks': [], 'sectors': [], 'ranking_intersections': [],
                  'note': '현재 수동 섹터 태그 기준. 섹터 간 중복 가능. 실패/미실행은 빈 결과가 아님. 조회순위/조건 충족은 매수 신호가 아님.'}
        union, occurrences = {}, {}
        tags = _rows(db, '''SELECT t.stock_code,s.sector_id,s.sector_name FROM stock_sectors t
                           JOIN sectors s ON s.sector_id=t.sector_id''')
        tagmap = {}
        for tag in tags:
            tagmap.setdefault(tag['stock_code'], []).append(dict(tag))
        for run in runs:
            members = [dict(row) for row in _rows(db, 'SELECT stock_code,stock_name,raw_code FROM condition_members WHERE run_id=? ORDER BY stock_code', (run['id'],))]
            valid = run['status'] in ('success', 'empty')
            item = {k: run[k] for k in ('id','alias','name','seq','requested_at','completed_at','status','record_count','duplicate_count','error')}
            item['members'] = members if valid else None
            item['unclassified_stocks'] = [s for s in members if s['stock_code'] not in tagmap] if valid else None
            groups = {}
            for stock in members:
                code = stock['stock_code']
                union[code] = stock
                occurrences.setdefault(code, []).append(run['alias'])
                for tag in tagmap.get(code, []):
                    group = groups.setdefault(tag['sector_id'], {'sector_name': tag['sector_name'], 'stock_codes': set()})
                    group['stock_codes'].add(code)
            item['sectors'] = [{'sector_name': g['sector_name'], 'stock_count': len(g['stock_codes']), 'stock_codes': sorted(g['stock_codes'])} for g in groups.values()] if valid else None
            output['conditions'].append(item)
            # Use only a PRIOR, same-environment, non-test [0198] observation on the SAME KST date.
            # Expose age; no claim of simultaneity or automatic freshness threshold.
            prior = _rows(db, '''SELECT id,collected_at,status,record_count,qry_tp,scheduled_at FROM collections
                WHERE environment=? AND api_id='ka00198' AND test_mode=0 AND collected_at<=?
                AND substr(collected_at,1,10)=substr(?,1,10)
                ORDER BY collected_at DESC,rowid DESC LIMIT 1''', (environment, run['requested_at'], run['requested_at']))
            comparison = {'alias': run['alias'], 'condition_run_id': run['id'], 'ranking_collection': dict(prior[0]) if prior else None,
                          'status': 'missing', 'stocks': None}
            if valid and prior and prior[0]['status'] == 'success':
                ranking = _rows(db, 'SELECT stk_cd,stk_nm,rank FROM ranking_records WHERE collection_id=? ORDER BY rank', (prior[0]['id'],))
                codes = {s['stock_code'] for s in members}
                comparison.update(status='observed', stocks=[dict(s) for s in ranking if s['stk_cd'] in codes],
                                  age_seconds=(datetime.fromisoformat(run['requested_at']) - datetime.fromisoformat(prior[0]['collected_at'])).total_seconds())
            elif not valid:
                comparison['status'] = 'condition_unavailable'
            elif prior:
                comparison['status'] = 'ranking_' + prior[0]['status']
            output['ranking_intersections'].append(comparison)
        output['complete_batch'] = len(runs) == len(TARGETS) and all(r['status'] in ('success', 'empty') for r in runs)
        output['unique_stock_count'] = len(union)
        output['cross_condition_duplicate_stock_count'] = sum(len(v) > 1 for v in occurrences.values())
        output['duplicate_stocks'] = [{'stock_code': k, 'conditions': v} for k, v in sorted(occurrences.items()) if len(v) > 1]
        output['unclassified_stocks'] = [stock for code, stock in sorted(union.items()) if code not in tagmap]
        output['unclassified_stock_count'] = len(output['unclassified_stocks'])
        output['observed_aliases'] = [r['alias'] for r in runs if r['status'] in ('success','empty')]
        groups = {}
        for code in union:
            for tag in tagmap.get(code, []):
                groups.setdefault(tag['sector_name'], set()).add(code)
        output['sectors'] = [{'sector_name': name, 'stock_count': len(codes), 'stock_codes': sorted(codes)} for name,codes in sorted(groups.items())]
        return output
