"""Read-only Kiwoom authentication and ka00198 snapshot client.

Sources and deliberate limitations are recorded in docs/API_SPEC.md.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import re
import time

import requests

from .config import Credentials

KST = timezone(timedelta(hours=9))
LOG = logging.getLogger(__name__)
AUTH_CODES = {8005, 8031, 8103}
RATE_CODES = {1700, 1701, 1702}


class KiwoomError(Exception):
    """Safe diagnostic: never includes request bodies or raw server messages."""


@dataclass
class Token:
    value: str = field(repr=False)
    expires_at: datetime


@dataclass
class Snapshot:
    collected_at: datetime
    records: list[dict]
    pages: list[dict] = field(default_factory=list)


def _code(data: dict) -> int | None:
    value = data.get("return_code")
    if isinstance(value, bool):
        raise KiwoomError("응답 return_code 형식이 잘못되었습니다.")
    if value is None:
        return None
    try:
        return int(str(value))
    except ValueError:
        raise KiwoomError("응답 return_code 형식이 잘못되었습니다.") from None


def _auth_error(code: int | None, data: dict) -> bool:
    embedded = re.search(r"\[(\d{3,5}):|CODE=(\d{3,5})", str(data.get("return_msg", "")))
    return code in AUTH_CODES or bool(
        embedded and int(embedded.group(1) or embedded.group(2)) in AUTH_CODES
    )


class KiwoomClient:
    def __init__(self, credentials: Credentials):
        self.credentials = credentials
        # Only the two documented hosts; no arbitrary URL or order API interface.
        self.session = requests.Session()
        self.token: Token | None = None
        self._last_request: float | None = None

    def close(self) -> None:
        self.session.close()

    def _post(self, path: str, body: dict, headers: dict, *, attempts=3):
        for attempt in range(attempts):
            # Conservative client policy, NOT a claimed official rate limit.
            if self._last_request is not None:
                time.sleep(max(0, 1 - (time.monotonic() - self._last_request)))
            self._last_request = time.monotonic()
            try:
                response = self.session.post(
                    self.credentials.base_url + path,
                    json=body,
                    headers={"Content-Type": "application/json;charset=UTF-8", **headers},
                    timeout=(5, 20),
                    allow_redirects=False,
                )
            except requests.exceptions.SSLError:
                raise KiwoomError("TLS 인증서 검증 실패. 인증서/프록시 설정을 확인하세요.") from None
            except (requests.Timeout, requests.ConnectionError):
                if attempt == attempts - 1:
                    raise KiwoomError(f"네트워크 연결/시간 초과: 최대 {attempts}회 시도 후 중단했습니다.") from None
                LOG.warning("네트워크 오류: 재시도 %d/2", attempt + 1)
                time.sleep(2 ** attempt)
                continue
            except requests.RequestException:
                raise KiwoomError("HTTP 요청을 수행하지 못했습니다.") from None
            try:
                data = response.json()
            except ValueError:
                data = None
            code = _code(data) if isinstance(data, dict) else None
            retryable = response.status_code in (429, 500, 502, 503, 504) or (
                response.status_code == 200 and code in RATE_CODES
            )
            if retryable:
                if attempt == attempts - 1:
                    raise KiwoomError(f"호출 제한 또는 서버 오류: 최대 {attempts}회 시도 후 중단했습니다.")
                delay = 2 ** attempt
                retry_after = response.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    if int(retry_after) > 60:
                        raise KiwoomError("서버 대기 요청이 60초를 초과합니다. 나중에 다시 실행하세요.")
                    delay = max(delay, int(retry_after))
                LOG.warning("호출 제한/서버 오류: %d초 후 재시도", delay)
                time.sleep(delay)
                continue
            return response, data
        raise AssertionError("unreachable")

    @staticmethod
    def _check(response, data) -> dict:
        if not 200 <= response.status_code < 300:
            raise KiwoomError(f"HTTP {response.status_code}: API 요청 실패. 접근 권한/네트워크를 확인하세요.")
        if not isinstance(data, dict):
            raise KiwoomError("API 응답이 JSON 객체가 아닙니다.")
        code = _code(data)
        if code not in (None, 0):
            raise KiwoomError(f"키움 API 오류 return_code={code}. 공식 오류 명세를 확인하세요.")
        return data

    def authenticate(self, *, attempts=3) -> Token:
        response, data = self._post(
            "/oauth2/token",
            {"grant_type": "client_credentials", "appkey": self.credentials.app_key,
             "secretkey": self.credentials.secret_key},
            {},  # Official core/auth.py issues tokens without authorization or api-id.
            attempts=attempts,
        )
        data = self._check(response, data)
        value = data.get("token")
        if not isinstance(value, str) or not value.strip() or str(data.get("token_type", "")).lower() != "bearer":
            raise KiwoomError("인증 응답의 token/token_type을 확인할 수 없습니다.")
        try:
            expires_at = datetime.strptime(data["expires_dt"], "%Y%m%d%H%M%S").replace(tzinfo=KST)
        except (KeyError, TypeError, ValueError):
            raise KiwoomError("인증 응답의 expires_dt 형식을 확인할 수 없습니다.") from None
        if expires_at <= datetime.now(KST):
            raise KiwoomError("발급된 토큰이 이미 만료되었습니다. PC 시각과 인증 설정을 확인하세요.")
        self.token = Token(value, expires_at)
        LOG.info("인증 성공 (토큰 만료: %s)", expires_at.isoformat())
        return self.token

    def _page(self, qry_tp: str, continuation: dict, *, single_request=False):
        for auth_attempt in range(1 if single_request else 2):
            if self.token is None or self.token.expires_at <= datetime.now(KST) + timedelta(seconds=30):
                self.authenticate(attempts=1 if single_request else 3)
            response, data = self._post(
                "/api/dostk/stkinfo", {"qry_tp": qry_tp},
                {"api-id": "ka00198", "authorization": "Bearer " + self.token.value, **continuation},
                attempts=1 if single_request else 3,
            )
            code = _code(data) if isinstance(data, dict) else None
            expired = response.status_code == 401 or (isinstance(data, dict) and _auth_error(code, data))
            if expired:
                self.token = None
                if auth_attempt == 0 and not single_request:
                    LOG.warning("인증 만료/무효 응답: 토큰 재발급 후 한 번 재조회합니다.")
                    continue
                raise KiwoomError("인증에 실패했습니다. 단일 요청 모드에서는 재조회하지 않습니다." if single_request else "토큰 재발급 후에도 인증에 실패했습니다.")
            return self._check(response, data), response.headers
        raise AssertionError("unreachable")

    def fetch_once(self, qry_tp: str = "1", *, single_request=False) -> Snapshot:
        if qry_tp not in {"1", "2", "3", "4", "5"}:
            raise ValueError("qry_tp는 1~5 중 하나여야 합니다.")
        collected_at = datetime.now(KST)
        records = []
        pages = []
        continuation = {}
        seen_keys = set()
        for _ in range(10):
            data, headers = self._page(qry_tp, continuation, single_request=single_request)
            pages.append(data)
            rows = data.get("item_inq_rank", [])
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise KiwoomError("item_inq_rank 응답 형식이 목록/객체가 아닙니다.")
            records.extend(rows)
            if headers.get("cont-yn") != "Y":
                break
            if single_request:
                raise KiwoomError("연속조회가 필요하지만 테스트는 조회 요청 1회로 제한됩니다. 불완전한 순위는 저장하지 않습니다.")
            key = headers.get("next-key")
            if not key or key in seen_keys:
                raise KiwoomError("연속조회 키가 누락되거나 반복되었습니다. 불완전한 결과를 출력하지 않습니다.")
            seen_keys.add(key)
            continuation = {"cont-yn": "Y", "next-key": key}
        else:
            raise KiwoomError("연속조회 10페이지를 초과했습니다. 결과 완전성을 확인하세요.")
        selected = []
        for row in records:
            # Preserve the API's rank; never manufacture ranks from list positions.
            try:
                rank = int(row["bigd_rank"])
            except (KeyError, TypeError, ValueError):
                raise KiwoomError("bigd_rank가 없거나 정수가 아닙니다. 상위 20위 판별을 중단합니다.") from None
            if rank <= 0:
                raise KiwoomError("bigd_rank가 0 이하입니다. 공식 설명이 모호하여 실제 의미 확인이 필요합니다.")
            if rank <= 20:
                selected.append(row)
        selected.sort(key=lambda row: int(row["bigd_rank"]))
        ranks = [int(row["bigd_rank"]) for row in selected]
        if len(set(ranks)) != len(ranks):
            raise KiwoomError("응답에 중복 순위가 있습니다. 실제 응답 확인이 필요합니다.")
        return Snapshot(collected_at, selected, pages)
