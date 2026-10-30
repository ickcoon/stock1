"""Reuse local Credentials/KiwoomClient; no shared token files or env mutation."""
from datetime import datetime,timedelta
from pathlib import Path
import re

from dotenv import dotenv_values
from .config import Credentials


class AuthBridgeError(ValueError):
    def __init__(self,reason):
        self.reason=reason
        super().__init__(reason)


def _settings(path):
    path=Path(path).resolve(strict=True)
    if not path.is_file():raise AuthBridgeError('credential_file_required')
    # Do not call load_dotenv: that changes os.environ and permits unrelated
    # inherited credentials to override the explicitly selected source.
    values=dotenv_values(path,encoding='utf-8-sig',interpolate=False)
    env=values.get('KIWOOM_ENV','')
    env=env.strip() if isinstance(env,str) else ''
    key=values.get('KIWOOM_APP_KEY','');secret=values.get('KIWOOM_SECRET_KEY','')
    key=key.strip() if isinstance(key,str) else '';secret=secret.strip() if isinstance(secret,str) else ''
    if '${' in key or '${' in secret:raise AuthBridgeError('credential_interpolation_not_supported')
    return env,key,secret


def check(path,*,environment):
    if environment not in ('mock','real'):raise AuthBridgeError('explicit_environment_required')
    declared,key,secret=_settings(path)
    ready=declared==environment and bool(key) and bool(secret)
    return dict(status='ready_for_separate_approval' if ready else 'not_ready',ready=ready,
        requested_environment=environment,declared_environment=declared if declared in ('mock','real') else 'missing_or_invalid',
        app_key_present=bool(key),secret_key_present=bool(secret),
        reason=None if ready else 'environment_mismatch' if declared!=environment else 'credentials_missing',
        oauth_calls=0,api_calls=0,cache_kind='new_client_memory_only',
        running_collector_token_accessed=False,credential_values_exported=False)


def credentials(path,*,environment):
    if environment not in ('mock','real'):raise AuthBridgeError('explicit_environment_required')
    declared,key,secret=_settings(path)
    if declared!=environment:raise AuthBridgeError('environment_mismatch')
    if not key or not secret:raise AuthBridgeError('credentials_missing')
    return Credentials(key,secret,environment=environment)


class OAuthProvider:
    """An isolated instance of the existing project authenticator.

    Future issuance can have server-side effects for the same App Key. The
    review flag is a prerequisite, NOT a guarantee of remote token coexistence.
    """
    def __init__(self,credentials,*,approved=False,token_impact_reviewed=False,client_factory=None):
        if approved is not True or token_impact_reviewed is not True:
            raise AuthBridgeError('oauth_approval_and_token_impact_review_required')
        self._credentials=credentials;self._factory=client_factory;self._client=None;self._token=None
        self.oauth_calls=0;self._stopped=False

    def get_token(self,*,minimum_valid_seconds=330):
        if not 0<=minimum_valid_seconds<=1830:raise AuthBridgeError('invalid_token_lifetime_budget')
        current=datetime.now().astimezone()
        if self._token is not None and self._token.expires_at>current+timedelta(seconds=minimum_valid_seconds):
            return self._token.value
        if self._stopped or self.oauth_calls:raise AuthBridgeError('token_expired_no_refresh')
        if self._factory is None:
            from .client import KiwoomClient
            self._factory=KiwoomClient
        try:
            self._client=self._factory(self._credentials)
            self.oauth_calls+=1
            token=self._client.authenticate(attempts=1)
            if (not isinstance(token.value,str) or not token.value or len(token.value)>4096
                    or re.search(r'\s',token.value) or token.expires_at.tzinfo is None
                    or token.expires_at<=datetime.now().astimezone()+timedelta(seconds=minimum_valid_seconds)):
                raise AuthBridgeError('token_lifetime_or_format_invalid')
            self._token=token
            return token.value
        except Exception:
            self._stopped=True
            raise AuthBridgeError('oauth_failed_or_token_invalid_no_retry') from None

    def close(self):
        if self._client is not None:
            self._client.token=None  # Our isolated instance only, never the collector.
            self._client.close()
        self._client=None;self._token=None;self._credentials=None;self._stopped=True
