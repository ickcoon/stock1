"""Explicit one-shot mock integration. Never opens an existing database."""
from contextlib import ExitStack, closing
from datetime import datetime, timedelta
import json
import math
import os
from pathlib import Path
import time

from .api_resource import api_resource
from .client import KST, DEFAULT_REQUEST_SPACING
from .collector import _attempt
from .errors import KiwoomError
from .quotes import BatchQuoteClient, QuoteDeadlineReached
from .scheduled_quotes import QuotePolicy, run_integrated_quotes
from .storage import Store, DEFAULT_DB

MOCK_HOST = 'https://mockapi.kiwoom.com'
WINDOW_SECONDS = 120


def fresh_database(path):
    if path is None:
        raise ValueError('quote-integrated-test에는 --db가 필요합니다.')
    supplied = Path(path)
    if supplied.is_symlink():
        raise ValueError('새 독립 테스트 DB 경로만 허용합니다.')
    target = supplied.resolve()
    if target == DEFAULT_DB.resolve():
        raise ValueError('기본 운영 DB 경로는 통합 테스트에 사용할 수 없습니다.')
    if target.suffix.lower() not in ('.db', '.sqlite', '.sqlite3') or not target.parent.is_dir():
        raise ValueError('기존 폴더 안에 새 .db/.sqlite/.sqlite3 경로를 지정하세요.')
    for suffix in ('', '-wal', '-shm', '-journal', '.mock.api.lock', '.real.api.lock'):
        if os.path.lexists(str(target)+suffix):
            raise ValueError('DB 또는 관련 파일이 이미 존재합니다. 기존 경로는 재사용하지 않습니다.')
    return target


def mock_environment(credentials, environment='mock'):
    if environment != 'mock' or credentials.environment != 'mock' or credentials.base_url != MOCK_HOST:
        raise ValueError('통합 테스트는 실제 적용 설정과 요청 환경 모두 mock이어야 합니다.')


class _BudgetSession:
    def __init__(self, transport, owner):
        self.transport, self.owner = transport, owner

    def post(self, url, **kwargs):
        owner = self.owner
        mock_environment(owner.credentials)
        api = kwargs.get('headers', {}).get('api-id')
        if url == MOCK_HOST+'/oauth2/token':
            kind, limit = 'auth', 1
        elif url == MOCK_HOST+'/api/dostk/stkinfo' and api in ('ka00198', 'ka10095'):
            kind, limit = ('ranking', 1) if api == 'ka00198' else ('quotes', 10)
        else:
            owner.stop_reason = 'request_not_allowed'
            raise KiwoomError('통합 테스트의 허용 요청 범위를 벗어났습니다.')
        if owner.request_counts[kind] >= limit:
            owner.stop_reason = 'request_limit'
            raise KiwoomError('통합 테스트의 요청 횟수 제한에 도달했습니다.')
        if owner.clock()+30 >= owner.ends_at:
            owner.stop_reason = 'time_budget'
            raise QuoteDeadlineReached('통합 테스트 요청 시간 예산이 부족합니다.')
        owner.request_counts[kind] += 1  # attempts, including transmission-unknown
        return self.transport.post(url, **kwargs)

    def close(self):
        self.transport.close()


class IntegratedQuoteClient(BatchQuoteClient):
    """Same token/session for ranking and quotes; no authentication replay."""
    def __init__(self, credentials, *, clock=time.monotonic):
        mock_environment(credentials)
        super().__init__(credentials)
        self.clock = clock
        self.ends_at = clock()+WINDOW_SECONDS
        self.request_counts = {'auth': 0, 'ranking': 0, 'quotes': 0}
        self.stop_reason = None
        self.session = _BudgetSession(self.session, self)

    def _post(self, path, body, headers, *, attempts=1):
        return super()._post(path, body, headers, attempts=1)

    def authenticate(self, *, attempts=1):
        if self.request_counts['auth'] >= 1:
            self.stop_reason = 'authentication_replay_blocked'
            raise KiwoomError('통합 테스트는 인증을 재시도하지 않습니다.')
        return super().authenticate(attempts=1)


def _counts(db, identifier):
    count = db.execute('SELECT count(*) FROM ranking_records WHERE collection_id=?', (identifier,)).fetchone()[0]
    result = dict(target_count=count, success_count=0, partial_count=0, failed_count=0,
                  uncertain_count=0, uncollected_count=count, counts_verified=True)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='quote_observations'").fetchone():
        return result
    for row in db.execute("SELECT status,issues_json FROM quote_observations WHERE collection_id=? AND api_id='ka10095' AND test_mode=1", (identifier,)):
        if row['status'] == 'success': result['success_count'] += 1
        elif row['status'] == 'partial': result['partial_count'] += 1
        elif row['status'] == 'error': result['failed_count'] += 1
        elif row['status'] in ('running','interrupted'):
            if 'deadline_before_send' not in json.loads(row['issues_json'] or '[]'):
                result['uncertain_count'] += 1
    result['uncollected_count'] = count-sum(result[key] for key in ('success_count','partial_count','failed_count','uncertain_count'))
    return result


def integrated_test(path, credentials, *, environment='mock', spacing=DEFAULT_REQUEST_SPACING,
                    now=lambda: datetime.now(KST), clock=time.monotonic):
    target = fresh_database(path)
    mock_environment(credentials, environment)
    if not math.isfinite(spacing) or spacing < 1.1:
        raise ValueError('요청 간격은 유한한 1.1초 이상이어야 합니다.')
    started = clock()
    report = dict(collection_id=None, environment='mock', test_mode=True, status='error', reason=None,
                  target_count=None, success_count=None, partial_count=None, failed_count=None,
                  uncertain_count=None, uncollected_count=None, counts_verified=False)
    # Atomic path claim BEFORE Store, authentication or HTTP. Never delete it on failure.
    with target.open('xb'):
        pass
    client = None
    with ExitStack() as resources:
        resources.enter_context(api_resource(target, 'mock'))
        store = resources.enter_context(closing(Store(target)))
        client = resources.enter_context(closing(IntegratedQuoteClient(credentials, clock=clock)))
        client.request_interval = spacing
        created_at = now()
        client.request_deadline = created_at+timedelta(seconds=WINDOW_SECONDS)
        identifier = store.begin(created_at, 'mock', '1', test_mode=True)
        report['collection_id'] = identifier
        try:
            result = _attempt(client, store, identifier, '1', single_request=True)
            state = store.db.execute('SELECT status FROM collections WHERE id=?', (identifier,)).fetchone()[0]
            if result != 0:
                report['reason'] = client.stop_reason or 'ranking_failed'
            elif state == 'empty':
                report.update(status='empty', reason='empty_ranking')
            else:
                followup = run_integrated_quotes(target, client, identifier, created_at=created_at,
                    now=now, policy=QuotePolicy(spacing_seconds=spacing))
                report.update(status=followup['status'], reason=client.stop_reason or followup['reason'])
        except KeyboardInterrupt:
            report.update(status='uncertain', reason='user_interrupted')
        except Exception:
            # Do not echo arbitrary exceptions, server messages, credentials or response bodies.
            report.update(status='error', reason='collection_or_storage_failed')
        finally:
            try:
                report.update(_counts(store.db, identifier))
                if report['uncertain_count']:
                    report['status'] = 'uncertain'
            except Exception:
                report['counts_verified'] = False
            report['request_counts'] = dict(client.request_counts)
            report['http_attempt_count'] = sum(client.request_counts.values())
    report['elapsed_seconds'] = max(0, clock()-started)
    return report, 0 if report['status'] == 'complete' else 1
