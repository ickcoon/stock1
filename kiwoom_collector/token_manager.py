"""Bounded automatic renewal, reusing KiwoomClient.authenticate/expires_dt."""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import logging
import hashlib
import json
import re
import threading
import time

from .errors import TokenManagementError, TransportError

LOG = logging.getLogger(__name__)


class TokenManager:
    def __init__(self, issuer, *, credentials, cache=None, clock=None, sleep=time.sleep):
        self._scope = self.scope(credentials)
        if cache is not None and cache.binding != self._scope:
            raise TokenManagementError('cache_scope_mismatch')
        self.issuer = issuer
        self.cache = cache
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep
        self._mutex = threading.RLock()
        self._state = {}
        self._closed = False

    @staticmethod
    def scope(credentials):
        return hashlib.sha256(json.dumps([credentials.environment, credentials.app_key,
            credentials.secret_key], separators=(',', ':')).encode()).hexdigest()

    def check_scope(self, credentials):
        if self.scope(credentials) != self._scope:
            raise TokenManagementError('manager_scope_mismatch')

    @staticmethod
    def _valid(token, now, seconds):
        return (isinstance(token.value, str) and 0 < len(token.value) <= 4096
                and not re.search(r'\s', token.value) and token.expires_at.tzinfo is not None
                and token.expires_at > now+timedelta(seconds=seconds))

    def _token(self, state):
        from .client import Token
        try:
            if state.get('token') is None:
                return None
            token = Token(state['token'], datetime.fromisoformat(state['expires_at']))
            if token.expires_at.tzinfo is None:
                raise ValueError()
            return token
        except (ValueError, KeyError, TypeError):
            raise TokenManagementError('cached_token_invalid') from None

    def _save(self, state):
        self._state = state
        if self.cache is not None:
            self.cache.save(state)

    def get_token(self, *, minimum_valid_seconds=600, attempts=2, existing=None):
        if (type(attempts) is not int or not 1 <= attempts <= 2
                or type(minimum_valid_seconds) not in (int, float) or not 0 <= minimum_valid_seconds <= 1830):
            raise ValueError('invalid_token_refresh_policy')
        margin = max(600, minimum_valid_seconds)
        with self._mutex, self.cache.locked() if self.cache is not None else nullcontext():
            if self._closed:
                raise TokenManagementError('manager_closed')
            now = self.clock()
            if now.tzinfo is None:
                raise TokenManagementError('clock_timezone_required')
            state = (self.cache.load() or {}) if self.cache is not None else self._state
            self._state = state
            retry = state.get('retry_after')
            if retry:
                try:
                    retry = datetime.fromisoformat(retry)
                    if retry.tzinfo is None:raise ValueError()
                except (TypeError, ValueError):
                    raise TokenManagementError('cached_retry_time_invalid') from None
                if retry > now:raise TokenManagementError('refresh_cooldown')
            token = self._token(state)
            if token is None and not state and self.cache is None:
                token = existing
            if token is not None and self._valid(token, now, margin):
                self._state = dict(token=token.value, expires_at=token.expires_at.isoformat())
                return token
            # A durable in-flight marker also throttles a crash after issuance.
            self._save(dict(token=None, retry_after=(now+timedelta(seconds=60)).isoformat(), category='refresh_in_progress'))
            category = 'authentication_failed'
            for attempt in range(attempts):
                try:
                    token = self.issuer()  # Exactly one raw OAuth attempt per call.
                    if not self._valid(token, self.clock(), margin):
                        raise TokenManagementError('issued_token_lifetime_or_format_invalid')
                    self._save(dict(token=token.value, expires_at=token.expires_at.isoformat()))
                    LOG.info('토큰 갱신 성공 (만료: %s)', token.expires_at.isoformat())
                    return token
                except TokenManagementError:
                    raise
                except Exception as error:
                    retryable = isinstance(error, TransportError) and error.retryable
                    category = error.category if isinstance(error, TransportError) else 'authentication_failed'
                    LOG.warning('토큰 갱신 실패 category=%s attempt=%d/%d', category, attempt+1, attempts)
                    if not retryable or attempt+1 == attempts:
                        break
                    self.sleep(max(1,min(60,error.retry_after)))
            cooldown = 60 if category in ('network', 'server_or_rate_limit') else 300
            self._save(dict(token=None, retry_after=(self.clock()+timedelta(seconds=cooldown)).isoformat(), category=category))
            raise TokenManagementError(category) from None

    def invalidate(self, value, *, cooldown=False):
        with self._mutex, self.cache.locked() if self.cache is not None else nullcontext():
            state = (self.cache.load() or {}) if self.cache is not None else self._state
            # An older in-flight request must not erase another process's new token.
            if state.get('token') == value:
                replacement = dict(token=None)
                if cooldown:
                    replacement.update(retry_after=(self.clock()+timedelta(seconds=300)).isoformat(), category='authentication_rejected')
                self._save(replacement)

    def close(self):
        with self._mutex:
            self._closed = True
            self._state = {}
