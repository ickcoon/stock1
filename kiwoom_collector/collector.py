"""KST weekday scheduler. No exchange holiday calendar is implied."""
from datetime import datetime, timedelta
import json
import logging
import sqlite3
import time

from .client import KST, KiwoomError
from .errors import ResponseValidationError

LOG = logging.getLogger(__name__)


def next_open(now):
    now = now.astimezone(KST)
    opening = now.replace(hour=9, minute=0, second=0, microsecond=0)
    closing = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now.weekday() < 5 and opening <= now < closing:
        return now
    if now >= closing or now.weekday() >= 5:
        opening += timedelta(days=1)
    while opening.weekday() >= 5:
        opening += timedelta(days=1)
    return opening


def collect(client, store, environment, qry_tp='1', interval_seconds=300,
            now=lambda: datetime.now(KST), sleep=time.sleep):
    if interval_seconds < 60:
        raise ValueError("수집 간격은 최소 60초입니다.")
    due = now()
    while True:
        current = now()
        opening = next_open(current)
        if opening > current:
            LOG.info("장외 대기: 다음 평일 개장 %s (공휴일 자동 판별 없음)", opening.isoformat())
            sleep(min(60, (opening - current).total_seconds()))
            continue
        if current < due:
            sleep(min(60, (due - current).total_seconds()))
            continue
        identifier = store.begin(current, environment, qry_tp)
        if identifier is not None:
            _attempt(client, store, identifier, qry_tp)
        else:
            LOG.info("동일 수집 시각 중복: API 호출 및 저장 생략")
        # Skip missed cycles instead of making catch-up request bursts.
        finished = now()
        due = current + timedelta(seconds=interval_seconds)
        if due <= finished:
            due = finished + timedelta(seconds=interval_seconds)


def test_now(client, store, environment, qry_tp='1', now=lambda: datetime.now(KST)):
    """One ranking HTTP request, no scheduler, retry, or continuation."""
    identifier = store.begin(now(), environment, qry_tp, test_mode=True)
    if identifier is None:
        LOG.info("동일 시각의 수집 이력이 있어 테스트 호출/저장을 생략합니다.")
        return 0
    return _attempt(client, store, identifier, qry_tp, single_request=True)


def _attempt(client, store, identifier, qry_tp, *, single_request=False):
    source_stage = "api_request"
    try:
        snapshot = client.fetch_once(qry_tp, single_request=True) if single_request else client.fetch_once(qry_tp)
        source_stage = "db_store"
        store.finish(identifier, snapshot)
        LOG.info("%s수집 %s: %d건 저장 (%s)", "테스트 " if single_request else "", identifier,
                 len(snapshot.records), 'success' if snapshot.records else 'empty')
        return 0
    except ResponseValidationError as exc:
        _failure(client, store, identifier, "response_validation", str(exc), source_stage)
        return 1
    except KiwoomError as exc:
        _failure(client, store, identifier, "api_request", str(exc), source_stage)
        return 1
    except sqlite3.Error as exc:
        code = getattr(exc, 'sqlite_errorcode', None)
        code = str(code) if isinstance(code, int) else "unknown"
        # Do not echo sqlite's exception string; it can contain data or schema text.
        kind = "무결성 제약 실패 (중복 순위/종목·외래키·CHECK 확인)" if isinstance(exc, sqlite3.IntegrityError) else "저장 실패 (디스크·권한·잠금 확인)"
        constraints = {
            "UNIQUE constraint failed: ranking_records.collection_id, ranking_records.stk_cd": "collection_id+stk_cd 중복",
            "UNIQUE constraint failed: ranking_records.collection_id, ranking_records.rank": "collection_id+rank 중복",
            "FOREIGN KEY constraint failed": "collection_id 외래키 위반",
            "CHECK constraint failed: rank BETWEEN 1 AND 20": "rank 1~20 범위 위반",
        }
        if str(exc) in constraints:
            kind += ": " + constraints[str(exc)]
        _failure(client, store, identifier, "db_store", f"SQLite {kind}; sqlite_errorcode={code}", source_stage)
        if not single_request and not isinstance(exc, sqlite3.IntegrityError):
            raise
        return 1
    except ValueError:
        _failure(client, store, identifier, "response_validation", "비구조화 ValueError: 원문은 보안상 생략; 응답 형식 확인 필요", source_stage)
        return 1
    except KeyboardInterrupt:
        store.fail(identifier, "사용자 중단", interrupted=True)
        raise


def _failure(client, store, identifier, stage, message, source_stage):
    shape = getattr(client, 'last_diagnostic', None)
    diagnostic = dict(shape) if isinstance(shape, dict) else {}
    diagnostic['source_stage'] = source_stage
    LOG.error("수집 %s 실패 stage=%s: %s", identifier, stage, message)
    LOG.error("안전 진단(값·헤더·메시지 원문 제외): %s", json.dumps(diagnostic, ensure_ascii=False))
    try:
        store.fail(identifier, message, stage=stage, diagnostic=diagnostic)
    except sqlite3.Error:
        LOG.error("실패 원인도 DB에 기록하지 못했습니다. 이미 생성된 running 이력은 보존됩니다. DB 접근 상태를 확인하세요.")
        raise
