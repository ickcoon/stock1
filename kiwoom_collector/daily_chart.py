"""Step 7-B1: offline daily-chart contract/parser. No HTTP, OAuth or DB client."""
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext
import hashlib
import json
import re

from .leader_tracking import canonical, timestamp

VERSION = 'step7b1-v1'
API_PATH = '/api/dostk/chart'
LIST_KEYS = {'ka10081': 'stk_dt_pole_chart_qry', 'ka11081': 'result_list'}
SPEC_URLS = {api: 'https://openapi.kiwoom.com/m/guide/apiguide/07/' + api for api in LIST_KEYS}
CODE = re.compile(r'[0-9A-Z]{6}(?:_NX|_AL)?', re.ASCII)
NUM = re.compile(r'[+-]?[0-9]+(?:\.[0-9]+)?', re.ASCII)
PRICE_FIELDS = {'open': 'open_pric', 'high': 'high_pric', 'low': 'low_pric', 'close': 'cur_prc'}


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def day(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{8}', value, re.ASCII):
        raise ValueError('YYYYMMDD string required')
    return date(int(value[:4]), int(value[4:6]), int(value[6:]))


@dataclass(frozen=True)
class DailyRequest:
    api_id: str
    stock_code: str
    environment: str
    base_dt: str
    adjustment: str

    def __post_init__(self):
        if self.api_id not in LIST_KEYS or self.environment not in ('mock', 'real'):
            raise ValueError('Explicit daily API and mock/real required')
        if not isinstance(self.stock_code, str) or CODE.fullmatch(self.stock_code) is None:
            raise ValueError('Exact stock code string required')
        day(self.base_dt)
        if self.adjustment not in ('0', '1'):
            raise ValueError('Explicit original(0)/adjusted(1) request required')
        if self.environment == 'mock' and self.market != 'KRX':
            raise ValueError('Official mock chart support is KRX only')

    @property
    def market(self):
        return 'NXT' if self.stock_code.endswith('_NX') else 'SOR' if self.stock_code.endswith('_AL') else 'KRX'

    @property
    def body(self):
        return dict(stk_cd=self.stock_code, base_dt=self.base_dt, upd_stkpc_tp=self.adjustment)


@dataclass(frozen=True)
class DailyRow:
    index: int
    trade_date: date | None
    raw_json: str
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: Decimal | None
    turnover_raw: Decimal | None
    turnover_won: Decimal | None
    issues: tuple[str, ...]

    @property
    def raw(self):
        return json.loads(self.raw_json)

    @property
    def fingerprint(self):
        return fingerprint([self.index, self.raw_json])


@dataclass(frozen=True)
class DailyPage:
    request: DailyRequest
    request_started_at: datetime
    response_received_at: datetime
    raw_json: str
    rows: tuple[DailyRow, ...]
    cont_yn: str | None
    next_key: str | None
    response_api_id: str | None
    ordering: str
    issues: tuple[str, ...]
    source: str
    version: str = VERSION

    @property
    def fingerprint(self):
        return fingerprint([self.request.body, self.request.api_id, self.request.environment,
                            self.request_started_at.isoformat(), self.response_received_at.isoformat(),
                            self.raw_json, self.cont_yn, self.next_key, self.response_api_id, self.source, self.version])

    @property
    def units(self):
        return dict(price='KRW', volume='share', turnover='million_KRW',
                    status='documented_not_runtime_verified')

    @property
    def pagination(self):
        return 'more_available' if self.cont_yn == 'Y' else 'complete' if self.cont_yn == 'N' else 'unknown'

    @property
    def raw(self):
        return json.loads(self.raw_json)


def measurement(raw, field, issues, *, price=False, volume=False):
    value = raw.get(field)
    if value is None or isinstance(value, str) and not value.strip():
        issues.append('missing:' + field)
        return None
    if not isinstance(value, str) or len(value) > 20 or not NUM.fullmatch(value.strip()):
        issues.append('invalid_numeric:' + field)
        return None
    result = Decimal(value.strip())
    # Official normalization treats price signs as direction, never negative prices.
    if price:
        result = result.copy_abs()
        if not result:
            issues.append('zero_price:' + field)
            return None
    elif result < 0:
        issues.append('negative:' + field)
        return None
    if volume and (result != result.to_integral_value() or result >= Decimal('4294967295')):
        issues.append('volume_fractional_or_saturated')
        return None  # never reconstruct volume from turnover/close
    return result


def parse_response(body, *, request, response_environment, request_started_at,
                   response_received_at, source, http_status=200, cont_yn=None,
                   next_key=None, response_api_id=None):
    """Parse one saved page, keeping order, raw fields, unknowns and quarantine reasons."""
    start, end = timestamp(request_started_at), timestamp(response_received_at)
    if end < start or not source:
        raise ValueError('Valid actual receipt interval and source required')
    if response_environment != request.environment:
        raise ValueError('Mixed mock/real response')
    if type(http_status) is not int or http_status != 200:
        raise ValueError('Unsuccessful HTTP response')
    if not isinstance(body, dict) or type(body.get('return_code')) not in (int, str) or body['return_code'] not in (0, '0'):
        raise ValueError('Missing/failed API return code')
    # No stripping of market suffixes or numeric coercion: ambiguous echo is rejected.
    if body.get('stk_cd') != request.stock_code:
        raise ValueError('Exact response stock code mismatch/unverified')
    if response_api_id is not None and response_api_id != request.api_id:
        raise ValueError('Response API mismatch')
    raw_json = canonical(body)
    raw_rows = body.get(LIST_KEYS[request.api_id])
    if not isinstance(raw_rows, list):
        raise ValueError('Expected API-specific daily row array')
    page_issues = []
    if cont_yn not in (None, '', 'N', 'Y') or cont_yn == 'Y' and (not isinstance(next_key, str) or not next_key.strip()):
        page_issues.append('invalid_continuation')
    if next_key is not None and (not isinstance(next_key, str) or len(next_key) > 50):
        page_issues.append('invalid_next_key')
    rows = []
    for index, raw in enumerate(raw_rows):
        if not isinstance(raw, dict):
            raise ValueError('Daily row object required')
        issues = []
        try:
            trade_date = day(raw.get('dt'))
        except (ValueError, TypeError):
            trade_date = None
            issues.append('invalid_trade_date')
        if trade_date and (trade_date > day(request.base_dt) or trade_date > end.date()):
            issues.append('future_or_after_base_date')
        if 'stk_cd' in raw and raw['stk_cd'] != request.stock_code:
            issues.append('row_code_mismatch')
        prices = {name: measurement(raw, field, issues, price=True) for name, field in PRICE_FIELDS.items()}
        present = [v for v in prices.values() if v is not None]
        if (prices['high'] is not None and any(v > prices['high'] for v in present)
                or prices['low'] is not None and any(v < prices['low'] for v in present)):
            issues.append('inconsistent_ohlc')
        volume = measurement(raw, 'trde_qty', issues, volume=True)
        cash = measurement(raw, 'trde_prica', issues)
        with localcontext() as ctx:
            ctx.prec = 80
            won = cash * Decimal('1000000') if cash is not None else None
        rows.append(DailyRow(index, trade_date, canonical(raw), **prices, volume=volume,
                             turnover_raw=cash, turnover_won=won, issues=tuple(issues)))
    dates = [r.trade_date for r in rows]
    valid = [d for d in dates if d is not None]
    if any(count > 1 for count in Counter(valid).values()):
        page_issues.append('duplicate_daily_dates')
    ordering = ('unknown' if len(valid) != len(dates) else 'single_or_empty' if len(dates) < 2 else
                'descending' if all(a > b for a, b in zip(dates, dates[1:])) else
                'ascending' if all(a < b for a, b in zip(dates, dates[1:])) else 'unordered')
    if ordering not in ('single_or_empty', 'descending'):
        page_issues.append('daily_order_unverified')
    return DailyPage(request, start, end, raw_json, tuple(rows), cont_yn, next_key,
                     response_api_id, ordering, tuple(page_issues), source)
