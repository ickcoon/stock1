"""Corrected ten-slot KST schedule. Never backfill missed slots."""
from datetime import date, datetime, timedelta
from pathlib import Path
import logging
import time
from contextlib import nullcontext
import sqlite3

from .client import KST
from .collector import _attempt
from .api_resource import api_resource
from .errors import KiwoomError
from .scheduled_quotes import run_scheduled_quotes, QuotePolicy

SLOTS = ('09:20', '09:30', '09:40', '09:50', '10:00', '11:00', '12:00', '13:00', '14:00', '15:00')
LOG = logging.getLogger(__name__)
MAX_LATENESS_SECONDS = 5


def load_holidays(path=None):
    if path is None:
        return frozenset()
    holidays = set()
    for number, line in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(), 1):
        text = line.split('#', 1)[0].strip()
        if not text:
            continue
        try:
            parsed = date.fromisoformat(text)
            if parsed.isoformat() != text:
                raise ValueError()
        except ValueError:
            raise ValueError(f'휴장일 파일 {number}행: YYYY-MM-DD 형식이 필요합니다.') from None
        holidays.add(parsed)
    return frozenset(holidays)


def slot_at(day, slot):
    return datetime.fromisoformat(f'{day.isoformat()}T{slot}:00').replace(tzinfo=KST)


def next_slot(now, holidays=frozenset()):
    now = now.astimezone(KST)
    day = now.date()
    while True:
        if day.weekday() < 5 and day not in holidays:
            for slot in SLOTS:
                target = slot_at(day, slot)
                if target >= now:
                    return target
        day += timedelta(days=1)


def collect_schedule(client, store, environment, qry_tp='1', holidays=frozenset(),
                     now=lambda: datetime.now(KST), sleep=time.sleep, *, with_quotes=False,
                     quote_policy=None):
    with api_resource(store.path, environment) if with_quotes else nullcontext():
        _collect_schedule(client, store, environment, qry_tp, holidays, now, sleep,
                          with_quotes, quote_policy or QuotePolicy())


def _collect_schedule(client, store, environment, qry_tp, holidays, now, sleep,
                      with_quotes, quote_policy):
    due = next_slot(now(), holidays)
    while True:
        current = now().astimezone(KST)
        if current < due:
            sleep(min(30, (due - current).total_seconds()))
            continue
        if (current - due).total_seconds() <= MAX_LATENESS_SECONDS:
            identifier = store.begin(current, environment, qry_tp, scheduled_at=due)
            if identifier is not None:
                LOG.info('고정 일정: 예정=%s 실제시작=%s', due.isoformat(), current.isoformat())
                result = _attempt(client, store, identifier, qry_tp)
                if with_quotes and result == 0 and store.db.execute(
                        'SELECT status FROM collections WHERE id=?', (identifier,)).fetchone()['status'] == 'success':
                    following = next_slot(due + timedelta(microseconds=1), holidays)
                    try:
                        report = run_scheduled_quotes(store.path, client, identifier,
                            environment=environment, created_at=current,
                            next_scheduled_at=following, now=now, policy=quote_policy)
                        LOG.info('후속 시세: 회차=%s 상태=%s 사유=%s', identifier, report['status'], report['reason'])
                    except (ValueError, KiwoomError, sqlite3.Error, OSError) as exc:
                        # Ranking success is already committed; never rewrite it.
                        LOG.error('후속 시세 중단: 회차=%s 오류유형=%s (순위 성공 보존)', identifier, type(exc).__name__)
            else:
                LOG.info('이미 시도한 예정 시각: 중복 호출/저장 생략 %s', due.isoformat())
        else:
            LOG.warning('예정 시각 누락: 소급 수집하지 않음 %s', due.isoformat())
        due = next_slot(max(now().astimezone(KST), due + timedelta(microseconds=1)), holidays)
