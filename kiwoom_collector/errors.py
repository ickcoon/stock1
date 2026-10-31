"""Errors with messages assembled from trusted labels, never response values."""


class KiwoomError(Exception):
    """API/auth/transport error with safe diagnostics."""


class ResponseValidationError(KiwoomError, ValueError):
    """Unexpected response structure/type; also compatible with ValueError callers."""


class TransportError(KiwoomError):
    def __init__(self, message, *, category, retryable=False, retry_after=1):
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.retry_after = retry_after


class TokenManagementError(KiwoomError):
    """Fixed category only: no upstream messages or token/credential values."""
    def __init__(self, category):
        self.category = category
        super().__init__('토큰 관리 실패: ' + category)
