"""Bounded real-only field diagnostic; no ranking parent or production schema."""
from contextlib import closing
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4

import requests

from .api_resource import api_resource
from .client import KiwoomClient, KST, DEFAULT_REQUEST_SPACING, _code
from .errors import KiwoomError
from .integrated_test import fresh_database
from .quotes import CODE, SPEC_VERSION

REAL_HOST = 'https://api.kiwoom.com'
WINDOW_SECONDS = 120
REQUEST_RESERVE_SECONDS = 30
NUMERIC = re.compile(r'[+-]?[0-9]+(?:\.[0-9]+)?', re.ASCII)
COMMON = ('cur_prc', 'flu_rt', 'trde_qty', 'trde_prica', 'open_pric', 'high_pric', 'low_pric')
FIELDS = {
    'ka10007': (*COMMON, 'pred_close_pric', 'smbol', 'date', 'tm'),
    'ka10095': (*COMMON, 'base_pric', 'pred_pre', 'pred_pre_sig', 'close_pric', 'dt', 'cntr_tm', 'bid_tm'),
}
REQUIRED = {
    'ka10007': (*COMMON, 'pred_close_pric', 'smbol', 'date', 'tm'),
    'ka10095': (*COMMON, 'base_pric', 'pred_pre', 'pred_pre_sig', 'dt', 'cntr_tm'),
}
DIRECTIONS = {'1': 'up', '2': 'up', '3': 'flat', '4': 'down', '5': 'down'}


class DiagnosticStop(Exception):
    def __init__(self, reason):
        self.reason = reason


def real_environment(credentials, environment):
    if environment != 'real' or credentials.environment != 'real' or credentials.base_url != REAL_HOST:
        raise ValueError('필드 검증은 명시적 real 환경과 실전 인증 설정만 허용합니다.')


def validate_options(path, codes, environment, spacing, budget, phase):
    if environment != 'real':
        raise ValueError('quote-fields-test에는 --environment real이 필요합니다.')
    if (not isinstance(codes, (tuple, list)) or len(codes) != 2
            or any(not isinstance(code, str) or CODE.fullmatch(code) is None for code in codes)
            or len(set(codes)) != 2
            or len('|'.join(codes)) > 20):
        raise ValueError('길이 20 이내의 서로 다른 종목코드 2개를 명시하세요.')
    if not math.isfinite(spacing) or spacing < 1.1:
        raise ValueError('요청 간격은 유한한 1.1초 이상이어야 합니다.')
    if not math.isfinite(budget) or not 60 <= budget <= 300:
        raise ValueError('시간 예산은 유한한 60~300초여야 합니다.')
    if phase not in ('unspecified', 'intraday', 'after-close'):
        raise ValueError('검증 시간대는 unspecified/intraday/after-close 중 하나입니다.')
    if path is None:
        raise ValueError('quote-fields-test에는 신규 --db 경로가 필요합니다.')
    return fresh_database(path)


def stamp(now):
    return now().astimezone(KST).isoformat(timespec='microseconds')


def decimal(value):
    if not isinstance(value, str) or NUMERIC.fullmatch(value.strip()) is None:
        return None
    try:
        return Decimal(value.strip())
    except InvalidOperation:
        return None


def text_number(value):
    return None if value is None else format(value, 'f')


def direction(value):
    return None if value is None else 'up' if value > 0 else 'down' if value < 0 else 'flat'


def observation(row, code, api, *, secrets=()):
    """Keep numeric/date market strings only; arbitrary server text is withheld."""
    raw, issues = {}, []
    for field in FIELDS[api]:
        value = row.get(field)
        if value is None or value == '':
            raw[field] = value
            if field in REQUIRED[api]:
                issues.append('missing:' + field)
            continue
        if (not isinstance(value, str) or len(value) > 64
                or any(secret and secret in value for secret in secrets)):
            raw[field] = None
            issues.append('invalid_or_redacted:' + field)
            continue
        if field in ('date', 'dt', 'tm', 'cntr_tm', 'bid_tm'):
            fmt, length = ('%Y%m%d', 8) if field in ('date', 'dt') else ('%H%M%S', 6)
            try:
                if len(value) != length or not value.isascii() or not value.isdigit():
                    raise ValueError()
                datetime.strptime(value, fmt)
            except ValueError:
                raw[field] = None
                issues.append('invalid_format:' + field)
                continue
        elif decimal(value) is None:
            raw[field] = None
            issues.append('invalid_numeric:' + field)
            continue
        raw[field] = value  # Do not remove signs, whitespace, zeros or decimal places.

    price, rate = decimal(raw.get('cur_prc')), decimal(raw.get('flu_rt'))
    volume, turnover = decimal(raw.get('trde_qty')), decimal(raw.get('trde_prica'))
    if volume is not None and (volume < 0 or volume != volume.to_integral_value()):
        issues.append('invalid_volume')
        volume = None
    if turnover is not None and turnover < 0:
        issues.append('invalid_turnover')
        turnover = None
    sign_key = 'smbol' if api == 'ka10007' else 'pred_pre_sig'
    symbol_direction = DIRECTIONS.get((raw.get(sign_key) or '').strip())
    if raw.get(sign_key) and symbol_direction is None:
        issues.append('unknown_direction_code')
    reference_key = 'pred_close_pric' if api == 'ka10007' else 'base_pric'
    reference = decimal(raw.get(reference_key))
    difference = calculated_rate = rate_error = None
    with localcontext() as context:
        context.prec = 100
        magnitude = abs(price) if price is not None else None
        if reference is not None and reference > 0 and magnitude is not None:
            difference = magnitude - reference
            calculated_rate = difference * 100 / reference
            if rate is not None:
                rate_error = abs(calculated_rate - rate)
        amount_won = turnover * 1000000 if turnover is not None else None
    normalized = dict(
        current_price_signed=text_number(price), current_price_abs_for_comparison=text_number(magnitude),
        price_status='unverified_sign_semantics', change_percent=text_number(rate),
        volume_shares=None if volume is None else int(volume), turnover_won=text_number(amount_won),
        expected_volume_unit='shares', expected_turnover_source_unit='million_KRW',
        unit_status='spec_declared_not_independently_verified',
        reference_field=reference_key, reference_price=text_number(reference),
        price_magnitude_minus_reference=text_number(difference),
        price_magnitude_direction=direction(difference), rate_direction=direction(rate),
        symbol_direction=symbol_direction, calculated_rate_percent=text_number(calculated_rate),
        rate_difference_percent_points=text_number(rate_error),
        rate_vs_reference=('unverified' if rate_error is None else
                           'consistent_with_diagnostic_tolerance' if rate_error <= Decimal('0.01') else 'different'),
        diagnostic_tolerance_percent_points='0.01', tolerance_is_official_rounding_rule=False,
        symbol_vs_rate=('unverified' if symbol_direction is None or rate is None else
                        'consistent' if symbol_direction == direction(rate) else 'different'),
        price_prefix=((raw.get('cur_prc') or '').strip()[:1] or None) if price is not None else None,
        api_date_raw=raw.get('date' if api == 'ka10007' else 'dt'),
        api_time_raw=raw.get('tm' if api == 'ka10007' else 'cntr_tm'),
        api_time_kind='unspecified_api_time' if api == 'ka10007' else 'execution_time',
        api_bid_time_raw=raw.get('bid_tm'))
    prefix = normalized['price_prefix']
    if prefix not in ('+', '-'):
        normalized['price_prefix'] = None
    normalized['price_prefix_vs_rate'] = (
        'unverified' if prefix not in ('+', '-') or rate is None or rate == 0 else
        'consistent' if (prefix == '+') == (rate > 0) else 'different')
    normalized['pred_pre_vs_reference'] = (
        'unverified' if api != 'ka10095' or difference is None or decimal(raw.get('pred_pre')) is None else
        'consistent' if decimal(raw['pred_pre']) == difference else 'different')
    return dict(code=code, api_id=api, status='partial' if issues else 'success',
                raw=raw, normalized=normalized, issues=issues)


def compare(code, observations):
    selected = {v['api_id']: v for v in observations if v['code'] == code}
    first, second = selected.get('ka10095'), selected.get('ka10007')
    fields = {}
    for key in ('current_price_abs_for_comparison', 'change_percent', 'volume_shares', 'turnover_won'):
        a = first['normalized'].get(key) if first else None
        b = second['normalized'].get(key) if second else None
        fields[key] = dict(ka10095=a, ka10007=b,
                           comparison='unverified' if a is None or b is None else
                           'equal' if Decimal(str(a)) == Decimal(str(b)) else 'different_observations')
    return dict(code=code, APIs_present=sorted(selected), fields=fields,
                status='unverified' if not first or not second else 'compared',
                observation_timing='sequential_not_atomic', price_sign_semantics='unverified',
                turnover_unit='spec_declared_not_independently_verified',
                reference_prices_equal=(None if not first or not second or
                    first['normalized']['reference_price'] is None or second['normalized']['reference_price'] is None else
                    Decimal(first['normalized']['reference_price']) == Decimal(second['normalized']['reference_price'])),
                requests={api: {k: value.get(k) for k in ('request_started_at', 'request_finished_at')}
                          for api, value in selected.items()})


def initialize(db):
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''
        CREATE TABLE field_runs (
            id TEXT PRIMARY KEY, environment TEXT NOT NULL CHECK(environment='real'),
            test_mode INTEGER NOT NULL CHECK(test_mode=1), phase TEXT NOT NULL,
            spec_version TEXT NOT NULL, codes_json TEXT NOT NULL, started_at TEXT NOT NULL,
            finished_at TEXT, status TEXT NOT NULL, reason TEXT, spacing_seconds REAL NOT NULL,
            time_budget_seconds REAL NOT NULL);
        CREATE TABLE field_requests (
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES field_runs(id),
            api_id TEXT NOT NULL, codes_json TEXT NOT NULL, status TEXT NOT NULL,
            started_at TEXT, finished_at TEXT, elapsed_seconds REAL, http_status INTEGER,
            return_code INTEGER, reason TEXT);
        CREATE TABLE field_observations (
            request_id TEXT NOT NULL REFERENCES field_requests(id), code TEXT NOT NULL,
            api_id TEXT NOT NULL, status TEXT NOT NULL, raw_json TEXT NOT NULL,
            normalized_json TEXT NOT NULL, issues_json TEXT NOT NULL,
            PRIMARY KEY(request_id,code));
        CREATE TABLE field_comparisons (
            run_id TEXT NOT NULL REFERENCES field_runs(id), code TEXT NOT NULL,
            comparison_json TEXT NOT NULL, PRIMARY KEY(run_id,code));
    ''')


class FieldClient(KiwoomClient):
    """Reuse auth and pacing; allow only this diagnostic's four calls, once each."""
    def __init__(self, credentials, *, clock, now, budget, on_start):
        real_environment(credentials, 'real')
        super().__init__(credentials)
        self.clock, self.now, self.on_start = clock, now, on_start
        self.ends_at = clock() + budget
        self.counts = {'auth': 0, 'ka10095': 0, 'ka10007': 0}
        self.expected = None
        self.started_at = self.finished_at = self.http_status = self.return_code = None
        self.transport_issue = None
        self.elapsed = None
        self._sent_single_codes = set()

    def _request(self, path, body, headers, *, before_send=None):
        try:
            real_environment(self.credentials, 'real')
        except ValueError:
            raise DiagnosticStop('environment_mismatch') from None
        api = 'auth' if path == '/oauth2/token' and not headers else headers.get('api-id')
        if api not in self.counts or self.expected is None or api != self.expected[0]:
            raise DiagnosticStop('request_not_allowed')
        codes = self.expected[1]
        if (api == 'auth' and self.counts['auth'] >= 1
                or api == 'ka10095' and (path != '/api/dostk/stkinfo' or self.counts[api] >= 1)
                or api == 'ka10007' and (path != '/api/dostk/mrkcond' or self.counts[api] >= 2
                                          or codes[0] in self._sent_single_codes)):
            raise DiagnosticStop('request_limit')
        if api != 'auth' and body != {'stk_cd': '|'.join(codes)}:
            raise DiagnosticStop('request_not_allowed')

        def ready():
            if self.clock() + REQUEST_RESERVE_SECONDS >= self.ends_at:
                raise DiagnosticStop('time_budget')
            if api != 'auth' and (self.token is None or self.token.expires_at <= self.now() + timedelta(seconds=30)):
                raise DiagnosticStop('token_expiring_no_refresh')
            self.on_start(stamp(self.now))  # Durable claim, before transmission, no open HTTP transaction.
            self.started_at = stamp(self.now)
            self.counts[api] += 1
            if api == 'ka10007':
                self._sent_single_codes.add(codes[0])

        start = None
        try:
            # Existing common _request invokes ready only after its perf_counter pacing.
            def before():
                nonlocal start
                ready()
                start = self.clock()
            response = super()._request(path, body, headers, before_send=before)
            self.finished_at = stamp(self.now)
            self.elapsed = max(0, self.clock() - start)
            self.http_status = response.status_code
            try:
                payload = response.json()
                self.return_code = _code(payload) if isinstance(payload, dict) else None
            except (ValueError, KiwoomError):
                pass
            return response
        except requests.exceptions.SSLError:
            self.transport_issue = 'tls_failed'
            raise
        except requests.RequestException:
            self.transport_issue = 'transmission_unknown'
            raise
        finally:
            if start is not None and self.finished_at is None:
                self.finished_at = stamp(self.now)
                self.elapsed = max(0, self.clock() - start)

    def _post(self, path, body, headers, *, attempts=1):
        response, data = super()._post(path, body, headers, attempts=1)
        self.return_code = _code(data) if isinstance(data, dict) else None
        return response, data

    def authenticate(self, *, attempts=1):
        if self.counts['auth']:
            raise DiagnosticStop('authentication_replay_blocked')
        return super().authenticate(attempts=1)


def _rows(data, api, codes):
    if not isinstance(data, dict) or _code(data) != 0:
        raise DiagnosticStop('invalid_or_failed_response')
    rows = data.get('atn_stk_infr') if api == 'ka10095' else [data]
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise DiagnosticStop('invalid_stock_list')
    actual = [row.get('stk_cd') for row in rows]
    if (len(actual) != len(codes) or any(not isinstance(code, str) for code in actual)
            or len(set(actual)) != len(actual) or set(actual) != set(codes)):
        raise DiagnosticStop('stock_code_mismatch_or_missing')
    return {row['stk_cd']: row for row in rows}


def fields_test(path, credentials, codes, *, environment='real', spacing=DEFAULT_REQUEST_SPACING,
                budget=WINDOW_SECONDS, phase='unspecified', clock=None, now=lambda: datetime.now(KST)):
    target = validate_options(path, codes, environment, spacing, budget, phase)
    real_environment(credentials, environment)
    clock = clock or time.perf_counter
    started, identifier = clock(), str(uuid4())
    report = dict(run_id=identifier, environment='real', test_mode=True, phase=phase,
                  status='error', reason=None, observations=[], comparisons=[], requests=None, counts_verified=False)
    with target.open('xb'):
        pass
    client = None
    with api_resource(target, 'real'), closing(sqlite3.connect(target, timeout=10)) as db:
        initialize(db)
        with db:
            db.execute('INSERT INTO field_runs VALUES(?,?,1,?,?,?,?,NULL,\'running\',NULL,?,?)',
                       (identifier, 'real', phase, SPEC_VERSION, json.dumps(codes), stamp(now), spacing, budget))
        active = None

        def on_start(at):
            with db:
                db.execute('UPDATE field_requests SET started_at=? WHERE id=?', (at, active))

        with closing(FieldClient(credentials, clock=clock, now=now,
                                 budget=budget-(clock()-started), on_start=on_start)) as client:
            client.request_interval = spacing
            steps = [('auth', []), ('ka10095', list(codes)), *[('ka10007', [code]) for code in codes]]
            try:
                for api, selected in steps:
                    if clock() + REQUEST_RESERVE_SECONDS >= client.ends_at:
                        raise DiagnosticStop('time_budget')
                    active = str(uuid4())
                    with db:
                        db.execute('INSERT INTO field_requests(id,run_id,api_id,codes_json,status) VALUES(?,?,?,?,\'running\')',
                                   (active, identifier, api, json.dumps(selected)))
                    client.expected = (api, selected)
                    client.started_at = client.finished_at = client.http_status = client.return_code = None
                    client.transport_issue = client.elapsed = None
                    if api == 'auth':
                        client.authenticate(attempts=1)
                        if client.return_code != 0:
                            raise DiagnosticStop('missing_auth_return_code')
                        observations = []
                    else:
                        endpoint = '/api/dostk/stkinfo' if api == 'ka10095' else '/api/dostk/mrkcond'
                        response, data = client._post(endpoint, {'stk_cd': '|'.join(selected)},
                            {'api-id': api, 'authorization': 'Bearer ' + client.token.value}, attempts=1)
                        client._check(response, data)
                        if response.headers.get('cont-yn') == 'Y':
                            raise DiagnosticStop('continuation_not_supported')
                        market_rows = _rows(data, api, selected)
                        secrets = (credentials.app_key, credentials.secret_key, client.token.value)
                        observations = [observation(market_rows[code], code, api, secrets=secrets) for code in selected]
                    with db:
                        request_status = 'partial' if any(v['issues'] for v in observations) else 'success'
                        db.execute('''UPDATE field_requests SET status=?,finished_at=?,elapsed_seconds=?,
                            http_status=?,return_code=? WHERE id=?''',
                            (request_status, client.finished_at, client.elapsed, client.http_status, client.return_code, active))
                        for value in observations:
                            db.execute('INSERT INTO field_observations VALUES(?,?,?,?,?,?,?)',
                                       (active, value['code'], api, value['status'], json.dumps(value['raw']),
                                        json.dumps(value['normalized']), json.dumps(value['issues'])))
                    active = None
                    if clock() > client.ends_at:
                        raise DiagnosticStop('time_budget_after_response')
                report['status'] = 'complete'
            except KeyboardInterrupt:
                report.update(status='uncertain' if client.started_at and active else 'stopped', reason='user_interrupted')
            except DiagnosticStop as error:
                report.update(status='stopped', reason=error.reason)
            except KiwoomError:
                reason = (client.transport_issue or ('http_limit_or_auth' if client.http_status in (401, 403, 429)
                          else 'authentication_failed' if client.expected[0] == 'auth' else 'api_or_response_failed'))
                report.update(status='uncertain' if reason == 'transmission_unknown' else 'error', reason=reason)
            except sqlite3.Error:
                report.update(status='uncertain' if client.started_at and active else 'error', reason='storage_failed')
            except Exception:
                report.update(status='uncertain' if client.started_at and active else 'error', reason='unexpected_error')
            finally:
                report['request_counts'] = dict(client.counts)
                report['http_attempt_count'] = sum(client.counts.values())
                try:
                    with db:
                        if active:
                            state = ('uncertain' if report['status'] == 'uncertain' else
                                     'not_sent' if client.started_at is None else 'error')
                            db.execute('''UPDATE field_requests SET status=?,finished_at=?,elapsed_seconds=?,
                                http_status=?,return_code=?,reason=? WHERE id=? AND status='running' ''',
                                (state, client.finished_at, client.elapsed, client.http_status, client.return_code, report['reason'], active))
                        rows = db.execute('''SELECT o.*,r.started_at,r.finished_at FROM field_observations o
                            JOIN field_requests r ON r.id=o.request_id WHERE r.run_id=? ORDER BY r.rowid,o.code''', (identifier,))
                        report['observations'] = [dict(code=r[1], api_id=r[2], status=r[3], raw=json.loads(r[4]),
                            normalized=json.loads(r[5]), issues=json.loads(r[6]), request_started_at=r[7], request_finished_at=r[8]) for r in rows]
                        report['comparisons'] = [compare(code, report['observations']) for code in codes]
                        for value in report['comparisons']:
                            db.execute('INSERT INTO field_comparisons VALUES(?,?,?)', (identifier, value['code'], json.dumps(value)))
                        if report['status'] == 'complete' and any(v['issues'] for v in report['observations']):
                            report['status'] = 'partial'
                        db.execute('UPDATE field_runs SET status=?,reason=?,finished_at=? WHERE id=?',
                                   (report['status'], report['reason'], stamp(now), identifier))
                    report['counts_verified'] = True
                    report['requests'] = [dict(zip(
                        ('api_id', 'codes', 'status', 'started_at', 'finished_at', 'elapsed_seconds',
                         'http_status', 'return_code', 'reason'),
                        (r[0], json.loads(r[1]), *r[2:]))) for r in db.execute('''
                            SELECT api_id,codes_json,status,started_at,finished_at,elapsed_seconds,
                                http_status,return_code,reason FROM field_requests WHERE run_id=? ORDER BY rowid''', (identifier,))]
                except (sqlite3.Error, ValueError, TypeError):
                    report.update(status='uncertain', reason='storage_failed', counts_verified=False)
    report['elapsed_seconds'] = max(0, clock() - started)
    report['observation_counts'] = {s: (sum(v['status'] == s for v in report['observations'])
                                      if report['counts_verified'] else None) for s in ('success', 'partial', 'error')}
    report['uncollected_observations'] = 4 - len(report['observations']) if report['counts_verified'] else None
    report['semantic_validation'] = 'unverified; comparisons are diagnostic evidence only'
    return report, 0 if report['status'] == 'complete' else 130 if report['reason'] == 'user_interrupted' else 1


def console_summary(report):
    counts = report['observation_counts']
    return (f"quote-fields-test: {report['status']} / real / test_mode=1 / "
            f"HTTP {report['http_attempt_count']}/4 / observations "
            f"{counts['success']} success, {counts['partial']} partial, "
            f"{report['uncollected_observations']} uncollected / "
            f"{report['elapsed_seconds']:.3f}s / sign and units remain unverified")
