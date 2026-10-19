"""Errors with messages assembled from trusted labels, never response values."""


class KiwoomError(Exception):
    """API/auth/transport error with safe diagnostics."""


class ResponseValidationError(KiwoomError, ValueError):
    """Unexpected response structure/type; also compatible with ValueError callers."""
