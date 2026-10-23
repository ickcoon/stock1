"""Explicit ka10007/ka10095 tests; never invoked by ranking schedulers.

Specification: docs/API_SPEC.md. Raw strings coexist with conservative normalization.
"""
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import sqlite3
import time
from decimal import Decimal, InvalidOperation, localcontext
from uuid import uuid4

import requests

from .client import KST, KiwoomClient, _code
from .errors import KiwoomError
from .api_resource import exclusive_quotes

SPEC_VERSION = '953e5dbff123f437ab4d11a78a95191a685eb51f'
VALUE_FIELDS = ('cur_prc', 'flu_rt', 'trde_qty', 'trde_prica',
                'open_pric', 'high_pric', 'low_pric', 'date', 'tm')
RAW_FIELDS = ('stk_cd', 'stk_nm', 'smbol', *VALUE_FIELDS)
# Preserve actual ranking codes, including observed six-character alphanumerics.
# This validates request syntax, not exchange eligibility; never invent a code.
CODE = re.compile(r'[0-9A-Z]{6}(?:_NX|_AL)?', re.ASCII)


def stamp():
    return datetime.now(KST).isoformat(timespec='microseconds')


@dataclass
class Quote:
    status: str = 'error'
    request_started_at: str | None = None
    request_finished_at: str | None = None
    http_status: int | None = None
    return_code: int | None = None
    raw: dict = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


class QuoteInterrupted(KeyboardInterrupt):
    def __init__(self, quote):
        self.quote = quote


class QuoteDeadlineReached(ValueError):
    """No quote HTTP was sent because the scheduling budget expired."""


class QuoteClient(KiwoomClient):
    def fetch_quote(self, stock_code):
        """At most one quote HTTP request, no retries/continuation/auth replay.

        Authentication is separate. Request times exclude auth and pacing waits;
        on authentication failure both request times remain NULL.
        """
        if not isinstance(stock_code, str) or CODE.fullmatch(stock_code) is None:
            raise ValueError('시세 요청 종목코드 형식을 확인하세요.')
        result = Quote()
        try:
            if self.token is None or self.token.expires_at <= datetime.now(KST) + timedelta(seconds=30):
                self.authenticate(attempts=1)
        except KiwoomError:
            result.issues = ['authentication_failed']
            return result
        if self._last_request is not None:
            time.sleep(max(0, 1 - (time.monotonic() - self._last_request)))
        self._last_request = time.monotonic()
        result.request_started_at = stamp()
        try:
            response = self.session.post(
                self.credentials.base_url + '/api/dostk/mrkcond',
                json={'stk_cd': stock_code},
                headers={'Content-Type': 'application/json;charset=UTF-8',
                         'api-id': 'ka10007', 'authorization': 'Bearer ' + self.token.value},
                timeout=(5, 20), allow_redirects=False,
            )
        except KeyboardInterrupt:
            result.status = 'interrupted'
            result.issues = ['request_interrupted']
            raise QuoteInterrupted(result) from None
        except requests.RequestException:
            result.issues = ['transport_failed']
            return result
        finally:
            result.request_finished_at = stamp()
        result.http_status = response.status_code
        if not 200 <= response.status_code < 300:
            result.issues = ['http_failed']
            return result
        try:
            data = response.json()
        except ValueError:
            result.issues = ['invalid_json']
            return result
        if not isinstance(data, dict):
            result.issues = ['invalid_response']
            return result
        try:
            result.return_code = _code(data)
        except KiwoomError:
            result.issues = ['invalid_return_code']
            return result
        if result.return_code != 0:
            result.issues = ['missing_return_code' if result.return_code is None else 'api_failed']
            return result
        if response.headers.get('cont-yn') == 'Y':
            result.issues = ['continuation_not_supported']
            return result
        # Never persist unknown fields, server messages, headers, or auth responses.
        result.raw = {key: data[key] for key in RAW_FIELDS if isinstance(data.get(key), str)}
        if 'stk_cd' in data and data['stk_cd'] != stock_code:
            result.raw = {}
            result.issues = ['stock_code_mismatch']
            return result
        for key in RAW_FIELDS:
            if key in data and not isinstance(data[key], str):
                result.issues.append('invalid_type:' + key)
        for key in VALUE_FIELDS:
            if key not in data or (isinstance(data[key], str) and not data[key].strip()):
                result.issues.append('missing:' + key)
        for key, fmt, length in (('date', '%Y%m%d', 8), ('tm', '%H%M%S', 6)):
            value = result.raw.get(key)
            if value:
                try:
                    if len(value) != length or not value.isascii() or not value.isdigit():
                        raise ValueError()
                    datetime.strptime(value, fmt)
                except ValueError:
                    result.issues.append('invalid_format:' + key)
        usable = any(result.raw.get(key, '').strip() for key in VALUE_FIELDS[:7])
        result.status = ('partial' if result.issues else 'success') if usable else 'error'
        return result


def targets(db, collection_id, environment, limit):
    if environment not in ('mock', 'real') or type(limit) is not int or limit not in (1, 2):
        raise ValueError('시세 테스트는 mock/real 환경과 1~2종목만 지원합니다.')
    parent = db.execute('SELECT * FROM collections WHERE id=?', (collection_id,)).fetchone()
    if parent is None or parent['api_id'] != 'ka00198' or parent['status'] != 'success':
        raise ValueError('성공한 ka00198 수집 회차 ID가 필요합니다.')
    if parent['environment'] != environment:
        raise ValueError('순위 회차와 시세 요청 환경이 다릅니다.')
    rows = db.execute('SELECT rank,stk_cd FROM ranking_records WHERE collection_id=? ORDER BY rank LIMIT ?',
                      (collection_id, limit)).fetchall()
    if not rows or any(not isinstance(r['stk_cd'], str) or CODE.fullmatch(r['stk_cd']) is None for r in rows):
        raise ValueError('선택한 순위의 종목코드가 없거나 지원 형식이 아닙니다.')
    return rows


def normalize_quote(raw, api_id):
    """Exact decimal text; unverified price sign semantics never become abs(price)."""
    result = {'version': 1, 'price_status': 'unverified_sign_semantics',
              'current_price_won': None, 'open_price_won': None,
              'high_price_won': None, 'low_price_won': None,
              'change_percent': None, 'volume_shares': None, 'turnover_won': None,
              'turnover_source_unit': 'million_KRW',
              'api_date_raw': raw.get('dt' if api_id == 'ka10095' else 'date'),
              'api_time_raw': raw.get('cntr_tm' if api_id == 'ka10095' else 'tm'),
              'api_time_kind': 'execution_time' if api_id == 'ka10095' else 'api_time'}
    for source, target, multiplier in (('flu_rt', 'change_percent', 1),
                                        ('trde_prica', 'turnover_won', 1000000)):
        value = raw.get(source)
        if isinstance(value, str) and re.fullmatch(r'[+-]?[0-9]+(?:\.[0-9]+)?', value, re.ASCII):
            try:
                # Local context avoids rounding large official 20-character values.
                with localcontext() as context:
                    context.prec = max(40, len(value) + 10)
                    if source == 'flu_rt' or not value.startswith('-'):
                        result[target] = format(Decimal(value) * multiplier, 'f')
            except InvalidOperation:
                pass
    value = raw.get('trde_qty')
    if isinstance(value, str) and re.fullmatch(r'[0-9]+', value, re.ASCII):
        result['volume_shares'] = int(value)
    return result


class BatchQuoteClient(QuoteClient):
    request_interval = 1.0
    request_deadline = None

    def _check_deadline(self, budget):
        if self.request_deadline is not None and datetime.now(KST) + timedelta(seconds=budget) >= self.request_deadline:
            raise QuoteDeadlineReached('다음 순위 수집 보호: 시세 요청 생략')

    def fetch_quotes(self, codes):
        """Experimental ka10095, at most two codes and one HTTP request."""
        if (not codes or len(codes) > 2 or len(set(codes)) != len(codes)
                or any(not isinstance(c, str) or CODE.fullmatch(c) is None for c in codes)
                or len('|'.join(codes)) > 20):
            raise ValueError('ka10095 테스트 입력은 길이 20 이내의 고유 종목 1~2개여야 합니다.')
        common = Quote()
        self._check_deadline(60)  # authentication + pacing + one quote, conservative budget
        def failed(issue):
            return {c: Quote('error', common.request_started_at, common.request_finished_at,
                             common.http_status, common.return_code, issues=[issue]) for c in codes}
        try:
            if self.token is None or self.token.expires_at <= datetime.now(KST) + timedelta(seconds=30):
                self.authenticate(attempts=1)
        except KiwoomError:
            return failed('authentication_failed')
        if self._last_request is not None:
            time.sleep(max(0, self.request_interval - (time.monotonic() - self._last_request)))
        self._last_request = time.monotonic()
        self._check_deadline(30)
        common.request_started_at = stamp()
        try:
            response = self.session.post(self.credentials.base_url + '/api/dostk/stkinfo',
                json={'stk_cd': '|'.join(codes)},
                headers={'Content-Type': 'application/json;charset=UTF-8',
                         'api-id': 'ka10095', 'authorization': 'Bearer ' + self.token.value},
                timeout=(5,20), allow_redirects=False)
        except KeyboardInterrupt:
            raise QuoteInterrupted(common) from None
        except requests.RequestException:
            common.request_finished_at = stamp()
            return failed('transport_failed')
        finally:
            common.request_finished_at = stamp()
        common.http_status = response.status_code
        if not 200 <= response.status_code < 300:
            return failed('http_failed')
        try:
            data = response.json()
            if not isinstance(data, dict):
                return failed('invalid_response')
            common.return_code = _code(data)
        except (ValueError, KiwoomError):
            return failed('invalid_response')
        if common.return_code != 0:
            specific = common.return_code
            embedded = re.search(r'\[(\d{3,5}):|CODE=(\d{3,5})', str(data.get('return_msg', '')))
            if embedded:
                specific = int(embedded.group(1) or embedded.group(2))
            issue = ('mock_unsupported' if specific == 8104 else 'rate_limited'
                     if specific in (1700,1701,1702) else 'authentication_failed'
                     if specific in (8001,8002,8003,8005,8006,8009,8010,8011,8012,8015,8016,
                                     8030,8031,8040,8050,8103) else 'api_failed')
            return failed(issue)
        if response.headers.get('cont-yn') == 'Y':
            return failed('continuation_not_supported')
        rows = data.get('atn_stk_infr')
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            return failed('invalid_stock_list')
        if any(not isinstance(r.get('stk_cd'), str) or r['stk_cd'] not in codes for r in rows):
            return failed('unexpected_stock_code')
        results = failed('missing_stock')
        fields = ('stk_cd', 'stk_nm', 'pred_pre_sig', *VALUE_FIELDS[:7], 'dt', 'cntr_tm', 'bid_tm')
        for code in codes:
            matches = [r for r in rows if r['stk_cd'] == code]
            if len(matches) > 1:
                results[code].issues = ['duplicate_stock_code']
                continue
            if not matches:
                continue
            row = matches[0]
            quote = results[code]
            quote.issues = []
            quote.raw = {k: row[k] for k in fields if isinstance(row.get(k), str)}
            for key in fields:
                if key in row and not isinstance(row[key], str):
                    quote.issues.append('invalid_type:' + key)
            for key in (*VALUE_FIELDS[:7], 'dt', 'cntr_tm'):
                if not isinstance(row.get(key), str) or not row[key].strip():
                    quote.issues.append('missing:' + key)
            for key, fmt, length in (('dt','%Y%m%d',8), ('cntr_tm','%H%M%S',6)):
                value = quote.raw.get(key)
                if value:
                    try:
                        if len(value) != length or re.fullmatch('[0-9]+', value, re.ASCII) is None:
                            raise ValueError()
                        datetime.strptime(value, fmt)
                    except ValueError:
                        quote.issues.append('invalid_format:' + key)
            usable = any(quote.raw.get(k, '').strip() for k in VALUE_FIELDS[:7])
            quote.status = ('partial' if quote.issues else 'success') if usable else 'error'
        return results


def migrate_quotes(db):
    """Caller owns BEGIN IMMEDIATE; transactional, additive, no common Store migration."""
    old = db.execute("SELECT sql FROM sqlite_master WHERE name='quote_batches'").fetchone()
    rebuilding = old is not None and "CHECK(api_id='ka10007')" in old[0]
    if rebuilding:
        db.execute('DROP TRIGGER IF EXISTS quote_batch_environment')
        db.execute('ALTER TABLE quote_observations RENAME TO quote_observations_v1')
        db.execute('ALTER TABLE quote_batches RENAME TO quote_batches_v1')
    db.execute('''CREATE TABLE IF NOT EXISTS quote_batches (
        id TEXT PRIMARY KEY, collection_id TEXT NOT NULL REFERENCES collections(id),
        environment TEXT NOT NULL CHECK(environment IN ('mock','real')),
        test_mode INTEGER NOT NULL DEFAULT 1 CHECK(test_mode=1),
        api_id TEXT NOT NULL DEFAULT 'ka10007' CHECK(api_id IN ('ka10007','ka10095')),
        spec_version TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
        status TEXT NOT NULL CHECK(status IN ('running','success','partial','error','interrupted','skipped')),
        UNIQUE(id,collection_id))''')
    columns = ','.join(name + ' TEXT' for name in VALUE_FIELDS)
    db.execute('''CREATE TABLE IF NOT EXISTS quote_observations (
        batch_id TEXT NOT NULL, collection_id TEXT NOT NULL, rank INTEGER NOT NULL,
        requested_code TEXT NOT NULL,
        api_id TEXT NOT NULL DEFAULT 'ka10007' CHECK(api_id IN ('ka10007','ka10095')),
        test_mode INTEGER NOT NULL DEFAULT 1 CHECK(test_mode=1),
        status TEXT NOT NULL CHECK(status IN ('running','success','partial','error','interrupted')),
        request_started_at TEXT, request_finished_at TEXT,
        http_status INTEGER, return_code INTEGER, raw_json TEXT, issues_json TEXT,
        ''' + columns + ''',
        PRIMARY KEY(collection_id,rank,api_id,test_mode),
        FOREIGN KEY(batch_id,collection_id) REFERENCES quote_batches(id,collection_id),
        FOREIGN KEY(collection_id,rank) REFERENCES ranking_records(collection_id,rank))''')
    if rebuilding:
        db.execute('INSERT INTO quote_batches SELECT * FROM quote_batches_v1')
        db.execute('INSERT INTO quote_observations SELECT * FROM quote_observations_v1')
        db.execute('DROP TABLE quote_observations_v1')
        db.execute('DROP TABLE quote_batches_v1')
    # Only new observations receive normalization; historical values are untouched.
    existing = {r[1] for r in db.execute('PRAGMA table_info(quote_observations)')}
    if 'normalized_json' not in existing:
        db.execute('ALTER TABLE quote_observations ADD COLUMN normalized_json TEXT')
    db.execute('''CREATE TABLE IF NOT EXISTS quote_requests (
        id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES quote_batches(id),
        requested_codes_json TEXT NOT NULL, started_at TEXT, finished_at TEXT,
        status TEXT NOT NULL, http_status INTEGER, return_code INTEGER,
        response_json TEXT, issues_json TEXT)''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS quote_batch_environment
        BEFORE INSERT ON quote_batches
        WHEN NOT EXISTS (SELECT 1 FROM collections WHERE id=NEW.collection_id
            AND environment=NEW.environment AND api_id='ka00198' AND status='success')
        BEGIN SELECT RAISE(ABORT,'quote parent mismatch'); END''')


@exclusive_quotes
def quote_test(path, client, collection_id, *, environment='mock', limit=1, api_id='ka10007'):
    """Observe existing top 1/2 now, not a reconstruction of the ranking timestamp.

    Claims survive failures/interruptions: repeating a target never repeats HTTP.
    Increasing limit from one to two claims only the previously unattempted target.
    """
    if client.credentials.environment != environment:
        raise ValueError('선택한 시세 환경과 KIWOOM_ENV가 다릅니다. 자동 전환하지 않습니다.')
    if api_id not in ('ka10007', 'ka10095') or (api_id == 'ka10095' and limit != 2):
        raise ValueError('복수 시세 테스트는 2종목만 지원합니다.')
    uri = Path(path).resolve().as_uri() + '?mode=rw'
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        # Validate before any schema writes. No database creation, WAL changes, or master migration.
        targets(db, collection_id, environment, limit)
        batch_id = str(uuid4())
        claimed = []
        try:
            db.execute('BEGIN IMMEDIATE')
            selected = targets(db, collection_id, environment, limit)
            locks = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quote_top20_locks'").fetchone()
            if locks and db.execute('SELECT 1 FROM quote_top20_locks WHERE environment=?', (environment,)).fetchone():
                raise ValueError('같은 환경의 TOP20 시세 실행이 진행/미완료 상태입니다.')
            if api_id == 'ka10095' and (len(selected) != 2 or len('|'.join(r['stk_cd'] for r in selected)) > 20):
                raise ValueError('복수 테스트는 요청 길이 20 이내의 2종목이 필요합니다.')
            migrate_quotes(db)
            db.execute('''INSERT INTO quote_batches(id,collection_id,environment,spec_version,started_at,status,api_id)
                VALUES(?,?,?,?,?,'running',?)''', (batch_id, collection_id, environment, SPEC_VERSION, stamp(), api_id))
            for row in selected:
                with closing(db.execute('''INSERT INTO quote_observations
                    (batch_id,collection_id,rank,requested_code,status,api_id) VALUES(?,?,?,?,'running',?)
                    ON CONFLICT DO NOTHING''',
                    (batch_id, collection_id, row['rank'], row['stk_cd'], api_id))) as cursor:
                    if cursor.rowcount:
                        claimed.append(dict(row))
            db.commit()
        except BaseException:
            db.rollback()
            raise
        results = []
        try:
            bulk = None
            if api_id == 'ka10095' and claimed:
                request_id = str(uuid4())
                with db:
                    db.execute("INSERT INTO quote_requests(id,batch_id,requested_codes_json,status) VALUES(?,?,?,'running')",
                               (request_id, batch_id, json.dumps([r['stk_cd'] for r in claimed])))
                bulk = client.fetch_quotes([r['stk_cd'] for r in claimed])
                with db:
                    sample = next(iter(bulk.values()))
                    db.execute('''UPDATE quote_requests SET started_at=?,finished_at=?,status=?,http_status=?,
                        return_code=?,response_json=?,issues_json=? WHERE id=?''',
                        (sample.request_started_at, sample.request_finished_at,
                         'success' if all(q.status == 'success' for q in bulk.values()) else 'partial'
                         if any(q.status in ('success','partial') for q in bulk.values()) else 'error',
                         sample.http_status, sample.return_code,
                         json.dumps({k:q.raw for k,q in bulk.items()}, ensure_ascii=False),
                         json.dumps({k:q.issues for k,q in bulk.items()}), request_id))
            for row in claimed:
                interruption = None
                try:
                    quote = bulk[row['stk_cd']] if bulk is not None else client.fetch_quote(row['stk_cd'])
                except QuoteInterrupted as exc:
                    quote = exc.quote
                    interruption = exc
                values = [quote.raw.get(key) for key in VALUE_FIELDS]
                with db:
                    db.execute('''UPDATE quote_observations SET status=?,request_started_at=?,request_finished_at=?,
                        http_status=?,return_code=?,raw_json=?,issues_json=?,normalized_json=?,''' +
                        ','.join(name + '=?' for name in VALUE_FIELDS) +
                        ' WHERE batch_id=? AND rank=? AND status=\'running\'',
                        (quote.status, quote.request_started_at, quote.request_finished_at,
                         quote.http_status, quote.return_code, json.dumps(quote.raw, ensure_ascii=False),
                         json.dumps(quote.issues), json.dumps(normalize_quote(quote.raw, api_id)),
                         *values, batch_id, row['rank']))
                results.append({'rank': row['rank'], 'status': quote.status, 'issues': quote.issues})
                if interruption is not None:
                    raise interruption
        except BaseException as exc:
            with db:
                if isinstance(exc, QuoteInterrupted):
                    db.execute('''UPDATE quote_requests SET started_at=?,finished_at=?
                        WHERE batch_id=? AND status='running' ''',
                        (exc.quote.request_started_at, exc.quote.request_finished_at, batch_id))
                    db.execute('''UPDATE quote_observations SET request_started_at=?,request_finished_at=?
                        WHERE batch_id=? AND status='running' ''',
                        (exc.quote.request_started_at, exc.quote.request_finished_at, batch_id))
                db.execute("UPDATE quote_observations SET status='interrupted',issues_json=? WHERE batch_id=? AND status='running'",
                           (json.dumps(['execution_interrupted']), batch_id))
                db.execute("UPDATE quote_batches SET status='interrupted',finished_at=? WHERE id=?", (stamp(), batch_id))
                db.execute("UPDATE quote_requests SET status='interrupted',finished_at=COALESCE(finished_at,?) WHERE batch_id=? AND status='running'", (stamp(), batch_id))
            raise
        states = [r['status'] for r in results]
        status = ('skipped' if not states else 'success' if all(s == 'success' for s in states)
                  else 'error' if all(s == 'error' for s in states) else 'partial')
        with db:
            db.execute('UPDATE quote_batches SET status=?,finished_at=? WHERE id=?', (status, stamp(), batch_id))
        report = {'batch_id': batch_id, 'collection_id': collection_id, 'environment': environment,
                  'test_mode': True, 'status': status, 'attempted': len(claimed),
                  'api_id': api_id, 'temporal_relation': 'current_quote_for_historical_ranking',
                  'skipped': len(selected) - len(claimed), 'observations': results}
        return report, 0 if status in ('success', 'skipped') else 1
