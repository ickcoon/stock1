"""Step 7-A offline observation engine. No collector, DB, UI or order access."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from typing import Protocol

KST = timezone(timedelta(hours=9))
VERSION = 'step7a-v1'


def timestamp(value):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Timezone-aware timestamp required')
    return result.astimezone(KST)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      default=lambda v: str(v) if isinstance(v, Decimal) else _unsupported(v))


def _unsupported(value):
    raise TypeError(type(value).__name__)


def number(value):
    if value is None:
        return None
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError('Finite nonnegative measurement required')
    return result


def percent(value, base):
    return (value / base - 1) * 100 if value is not None and base else None


class TradingCalendar(Protocol):
    def offset(self, origin: date, sessions: int) -> date | None: ...


@dataclass(frozen=True)
class FixtureCalendar:
    """Every calendar date in coverage must explicitly be open or closed."""
    open_dates: tuple[date, ...]
    closed_dates: tuple[date, ...]
    source: str

    def __post_init__(self):
        object.__setattr__(self, 'open_dates', tuple(self.open_dates))
        object.__setattr__(self, 'closed_dates', tuple(self.closed_dates))
        if not self.source or set(self.open_dates) & set(self.closed_dates):
            raise ValueError('Calendar source/conflicting dates')

    def offset(self, origin, sessions):
        if origin not in self.open_dates:
            return None
        cursor = origin
        direction = 1 if sessions >= 0 else -1
        remaining = abs(sessions)
        while remaining:
            cursor += timedelta(days=direction)
            if cursor in self.open_dates:
                remaining -= 1
            elif cursor not in self.closed_dates:
                return None
        return cursor


@dataclass(frozen=True)
class Observation:
    stock_code: str
    environment: str
    market: str
    trade_date: date
    market_at: datetime
    available_at: datetime
    phase: str
    source: str
    status: str = 'received'
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    volume: Decimal | None = None
    turnover_won: Decimal | None = None
    # Exact sum(price * volume), not inferred from a quote or typical OHLC price.
    price_volume_sum: Decimal | None = None
    vwap_volume: Decimal | None = None
    raw_json: str = '{}'
    version: str = VERSION

    def __post_init__(self):
        for key in ('market_at', 'available_at'):
            object.__setattr__(self, key, timestamp(getattr(self, key)))
        if (self.environment not in ('mock', 'real') or not self.market or not self.source
                or not self.stock_code or self.phase not in ('pre_market', 'intraday', 'post_market')
                or self.status not in ('received', 'halted', 'not_received')):
            raise ValueError('Invalid observation identity/status')
        if self.market_at.date() != self.trade_date or self.market_at > self.available_at:
            raise ValueError('Invalid observation chronology')
        for key in ('open', 'high', 'low', 'close', 'volume', 'turnover_won',
                    'price_volume_sum', 'vwap_volume'):
            object.__setattr__(self, key, number(getattr(self, key)))
        prices = [v for v in (self.open, self.high, self.low, self.close) if v is not None]
        if any(v == 0 for v in prices):
            raise ValueError('Prices must be positive')
        if (self.high is not None and any(v > self.high for v in prices)
                or self.low is not None and any(v < self.low for v in prices)):
            raise ValueError('Inconsistent OHLC')
        object.__setattr__(self, 'raw_json', canonical(json.loads(self.raw_json)))

    @property
    def fingerprint(self):
        return hashlib.sha256(canonical({k: str(getattr(self, k))
                                        for k in self.__dataclass_fields__}).encode()).hexdigest()


def bind(event, code, environment, market):
    if (event.stock_code, event.environment, event.market) != (code, environment, market):
        raise ValueError('Stock/environment/market mismatch')


@dataclass(frozen=True)
class Snapshot:
    stock_code: str
    environment: str
    market: str
    d0: date
    captured_at: datetime
    created_at: datetime
    payload_json: str
    d0_bar: Observation | None = None
    version: str = VERSION

    def __post_init__(self):
        for key in ('captured_at', 'created_at'):
            object.__setattr__(self, key, timestamp(getattr(self, key)))
        if (self.environment not in ('mock', 'real') or not self.market or not self.stock_code
                or self.captured_at.date() != self.d0 or self.created_at < self.captured_at):
            raise ValueError('Invalid D0 identity/chronology')
        object.__setattr__(self, 'payload_json', canonical(json.loads(self.payload_json)))
        if self.d0_bar:
            bind(self.d0_bar, self.stock_code, self.environment, self.market)
            if (self.d0_bar.trade_date != self.d0 or self.d0_bar.phase != 'post_market'
                    or self.d0_bar.status != 'received'
                    or self.d0_bar.available_at > self.captured_at):
                raise ValueError('D0 closing bar not confirmed at selection')

    @property
    def episode_id(self):
        # One episode per code/D0/environment/market, regardless of later revisions.
        return hashlib.sha256(canonical([self.stock_code, str(self.d0), self.environment,
                                         self.market]).encode()).hexdigest()

    @property
    def payload(self):
        return json.loads(self.payload_json)  # fresh copy; no mutable nested state


def snapshot_from_review(report, *, stock_code, captured_at, created_at, market,
                         stock_name=None, primary_sector=None, d0_bar=None):
    """Consume the saved Step 6-D eligibility report, not live collector objects.

    Missing values stay null. Retain the complete original row and source evidence.
    """
    at = timestamp(captured_at)
    rows = [r for r in report['rows'] if r['stock_code'] == stock_code]
    if len(rows) != 1:
        raise ValueError('Exactly one matching review row required')
    row = rows[0]
    sources = report['sources']
    env = sources['ranking']['environment']
    for source in (*sources.values(), *row.get('source_times', {}).values()):
        if source.get('environment') != env:
            raise ValueError('Mixed source environments')
        for key in ('collected_at', 'request_started_at', 'response_received_at', 'market_asof'):
            if source.get(key) is not None and timestamp(source[key]) > at:
                raise ValueError('Future D0 source')
    cap = row.get('capital_reference') or {}
    if cap.get('requested_code', stock_code) != stock_code or cap.get('raw', {}).get('stk_cd', stock_code) != stock_code:
        raise ValueError('Capital stock mismatch')
    for key in ('retrieved_at', 'market_asof', 'request_started_at', 'response_received_at'):
        if cap.get(key) and timestamp(cap[key]) > at:
            raise ValueError('Future capital source')
    if cap.get('environment', env) != env:
        raise ValueError('Mixed capital environment')
    payload = dict(stock_name=stock_name, primary_sector=primary_sector,
        sector_tags=row.get('sectors'), ranking_0198=row.get('ranking_0198'),
        top5_appearances=row.get('top5_appearances'), turnover_rank=row.get('turnover_rank'),
        turnover_raw=row.get('turnover_raw'), turnover_won=row.get('turnover_won_diagnostic'),
        market_cap_won=row.get('market_cap_won_verified'), capital_band=row.get('capital_band'),
        change_percent=row.get('change_percent_raw'), qualification=row.get('qualification'),
        candidate_confirmed=row.get('candidate_confirmed'), original_row=row, sources=sources,
        is_execution_record=False)
    return Snapshot(stock_code, env, market, at.date(), at, timestamp(created_at), canonical(payload), d0_bar)


@dataclass(frozen=True)
class PatternConfig:
    volume_drop_percent: Decimal = Decimal('20')
    correction_min_percent: Decimal = Decimal('0.5')
    correction_max_percent: Decimal = Decimal('10')
    ma_near_percent: Decimal = Decimal('2')

    def __post_init__(self):
        for key in self.__dataclass_fields__:
            object.__setattr__(self, key, number(getattr(self, key)))
        if self.volume_drop_percent > 100 or self.correction_min_percent > self.correction_max_percent:
            raise ValueError('Invalid pattern thresholds')


def analyze(snapshot, events, *, calendar: TradingCalendar, as_of, config=PatternConfig()):
    """Point-in-time projection. Future events are excluded, never backfilled."""
    cutoff = timestamp(as_of)
    if cutoff < snapshot.captured_at:
        raise ValueError('Snapshot not yet available')
    visible = []
    for event in events:
        bind(event, snapshot.stock_code, snapshot.environment, snapshot.market)
        if event.available_at <= cutoff and event.market_at <= cutoff:
            visible.append(event)
    baseline = snapshot.d0_bar
    # A late D0 close remains a separate event and never mutates the selection snapshot.
    if baseline:
        visible.append(baseline)
    days = []
    for offset in (1, 2):
        day = calendar.offset(snapshot.d0, offset)
        result = dict(offset=offset, trade_date=day.isoformat() if day else None,
                      status='calendar_unknown', patterns=(), metrics={}, source=None)
        days.append(result)
        if day is None:
            continue
        if day > cutoff.date():
            result['status'] = 'pending'
            continue
        candidates = [e for e in visible if e.trade_date == day]
        if not candidates:
            result['status'] = 'not_received'
            continue
        latest_time = max(e.available_at for e in candidates)
        latest = [e for e in candidates if e.available_at == latest_time]
        if len(set(latest)) != 1:
            raise ValueError('Ambiguous observations at identical availability time')
        current = latest[0]
        result.update(status=current.status, phase=current.phase, source=current.source,
                      market_at=current.market_at.isoformat(), available_at=current.available_at.isoformat())
        if current.status != 'received':
            continue
        # Baseline may become known after D0, but only through an explicit visible event.
        def closing_on(target):
            records = [e for e in visible if e.trade_date == target and e.phase == 'post_market'
                       and e.status == 'received' and e.available_at <= current.available_at
                       and e.market_at <= current.market_at]
            if not records:
                return None
            newest = max(e.available_at for e in records)
            unique = set(e for e in records if e.available_at == newest)
            if len(unique) != 1:
                raise ValueError('Ambiguous historical close')
            return unique.pop()
        d0 = baseline or closing_on(snapshot.d0)
        previous_day = calendar.offset(day, -1)
        previous = closing_on(previous_day) if previous_day else None
        m = {k: getattr(current, k) for k in ('open', 'high', 'low', 'close', 'volume', 'turnover_won')}
        m.update(change_previous_percent=percent(current.close, previous.close if previous else None),
                 change_d0_percent=percent(current.close, d0.close if d0 else None),
                 volume_d0_percent=percent(current.volume, d0.volume if d0 else None),
                 d0_high_break=(current.high > d0.high if d0 and d0.high and current.high else None),
                 d0_low_break=(current.low < d0.low if d0 and d0.low and current.low else None),
                 vwap=(current.price_volume_sum / current.vwap_volume
                       if current.price_volume_sum is not None and current.vwap_volume else None))
        inputs = {e.fingerprint: e for e in (current, d0, previous) if e is not None}
        for window in (3, 5):
            closes = [current.close] if current.phase == 'post_market' else []
            for back in range(1, window):
                prior_day = calendar.offset(day, -back)
                prior = closing_on(prior_day) if prior_day else None
                if prior is not None:
                    inputs[prior.fingerprint] = prior
                closes.append(prior.close if prior else None)
            ma = sum(closes) / window if len(closes) == window and all(v is not None for v in closes) else None
            m['ma' + str(window)] = ma
            m['ma' + str(window) + '_distance_percent'] = percent(current.close, ma)
        patterns = []
        change, volume = m['change_d0_percent'], m['volume_d0_percent']
        correction = change is not None and -config.correction_max_percent <= change <= -config.correction_min_percent
        if change is not None and change > 0 and m['change_previous_percent'] is not None and m['change_previous_percent'] > 0:
            patterns.append('uptrend_continuation')
        if m['d0_high_break'] is True:
            patterns.append('d0_high_breakout')
        if m['d0_low_break'] is True:
            patterns.append('d0_low_breakdown')
        if correction and volume is not None and volume <= -config.volume_drop_percent:
            patterns.append('correction_with_lower_volume')
        if correction and any(m['ma'+str(w)+'_distance_percent'] is not None and
                              abs(m['ma'+str(w)+'_distance_percent']) <= config.ma_near_percent for w in (3, 5)):
            patterns.append('correction_near_short_ma')
        if any(m[k] is None for k in ('open', 'high', 'low', 'close', 'volume',
                                      'change_d0_percent', 'ma3', 'ma5')):
            patterns.append('insufficient_data')
        result.update(metrics=m, patterns=tuple(patterns), inputs=[
            dict(fingerprint=e.fingerprint, source=e.source, trade_date=e.trade_date.isoformat(),
                 market_at=e.market_at.isoformat(), available_at=e.available_at.isoformat(),
                 environment=e.environment, market=e.market, version=e.version)
            for e in sorted(inputs.values(), key=lambda e: (e.trade_date, e.available_at))])
    complete = all(d['status'] == 'received' and d.get('phase') == 'post_market'
                   and all(d['metrics'].get(k) is not None for k in ('open', 'high', 'low', 'close', 'volume')) for d in days)
    return dict(episode_id=snapshot.episode_id, as_of=cutoff.isoformat(),
                state='completed' if complete else 'tracking', days=days, version=VERSION,
                config={k: str(getattr(config, k)) for k in config.__dataclass_fields__})


class MemoryRepository:
    """Explicitly ephemeral, isolated test store; no operational DB dependency."""
    def __init__(self):
        self._snapshots = {}
        self._events = {}

    def add_snapshot(self, snapshot):
        if snapshot.episode_id in self._snapshots:
            raise ValueError('Duplicate D0 episode')
        self._snapshots[snapshot.episode_id] = snapshot
        self._events[snapshot.episode_id] = ()

    def add_event(self, episode_id, event):
        snapshot = self._snapshots[episode_id]
        bind(event, snapshot.stock_code, snapshot.environment, snapshot.market)
        if event in self._events[episode_id]:
            raise ValueError('Duplicate event')
        self._events[episode_id] += (event,)

    def get(self, episode_id):
        return self._snapshots[episode_id], self._events[episode_id]
