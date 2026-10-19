"""KST weekday scheduler. No exchange holiday calendar is implied."""
from datetime import datetime, timedelta
import logging
import sqlite3
import time

from .client import KST, KiwoomError

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
            try:
                snapshot = client.fetch_once(qry_tp)
                store.finish(identifier, snapshot)
                LOG.info("수집 %s: %d건 저장", identifier, len(snapshot.records))
            except KiwoomError as exc:
                store.fail(identifier, str(exc))
                LOG.error("수집 %s 실패: %s (다음 주기에 재시도)", identifier, exc)
            except sqlite3.IntegrityError:
                store.fail(identifier, "종목/순위 중복 또는 DB 무결성 검증 실패")
                LOG.error("수집 %s: DB 무결성 검증 실패 (해당 회차 전체 롤백)", identifier)
            except ValueError:
                store.fail(identifier, "응답 필드/자료형 검증 실패")
                LOG.error("수집 %s: 응답 필드/자료형 검증 실패", identifier)
            except KeyboardInterrupt:
                store.fail(identifier, "사용자 중단", interrupted=True)
                raise
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
    try:
        snapshot = client.fetch_once(qry_tp, single_request=True)
        store.finish(identifier, snapshot)
        LOG.info("테스트 수집 %s: %d건 저장 후 종료 (%s)", identifier,
                 len(snapshot.records), 'success' if snapshot.records else 'empty')
        return 0
    except KiwoomError as exc:
        store.fail(identifier, str(exc))
        LOG.error("테스트 수집 %s 실패: %s (재시도 없이 종료)", identifier, exc)
        return 1
    except (ValueError, sqlite3.IntegrityError):
        store.fail(identifier, "응답 자료형 또는 DB 무결성 검증 실패")
        LOG.error("테스트 수집 %s: 검증 실패 후 종료", identifier)
        return 1
    except KeyboardInterrupt:
        store.fail(identifier, "사용자 중단", interrupted=True)
        raise
