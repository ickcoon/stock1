"""Optional ka10001 transport for the existing injected IndependentClient.

No order endpoints, CLI activation, query retries, or changes to the offline parser.
"""
from datetime import datetime

from .client import KiwoomClient, KST, _auth_error, _code
from .errors import KiwoomError
from .market_cap import API_ID, API_PATH, validate_request


class ManagedInfoTransport(KiwoomClient):
    def __init__(self, credentials, *, approved=False, token_manager=None, token_provider=None):
        if approved is not True:
            raise ValueError('approved_transport_required')
        if token_manager is not None and token_provider is not None:
            raise ValueError('choose_one_token_provider')
        if token_provider is not None:token_provider.check_scope(credentials)
        super().__init__(credentials, token_manager=token_manager)
        self.provider = token_provider  # Caller owns a shared OAuthProvider.

    def __call__(self, *, api_id, path, body, environment):
        if (api_id != API_ID or path != API_PATH or environment != self.credentials.environment
                or not isinstance(body, dict) or set(body) != {'stk_cd'}):
            raise ValueError('disallowed_info_request')
        validate_request(body['stk_cd'], environment)
        if self.provider is not None:
            value = self.provider.get_token(minimum_valid_seconds=600)
        else:
            value = self.ensure_token().value
        started = datetime.now(KST).isoformat()
        response, data = self._post(path, body, {'api-id':api_id, 'authorization':'Bearer '+value}, attempts=1)
        received = datetime.now(KST).isoformat()
        code = _code(data) if isinstance(data, dict) else None
        if response.status_code == 401 or (isinstance(data, dict) and _auth_error(code, data)):
            if self.provider is not None:self.provider.invalidate(value)
            else:self.invalidate_token(cooldown=True)
            raise KiwoomError('종목정보 인증 실패: 재조회하지 않습니다.')
        return dict(environment=environment, body=data, http_status=response.status_code,
                    request_started_at=started, response_received_at=received,
                    cont_yn=response.headers.get('cont-yn', 'N'))
