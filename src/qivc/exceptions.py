class QivcError(Exception):
    """Base exception for all QIVC errors."""


class QivcDataError(QivcError):
    """Raised when a data agent fails to fetch or parse data."""


class QivcRateLimitError(QivcDataError):
    """Raised when the SEC EDGAR rate limit (429/403) is hit and retries are exhausted."""


class QivcConfigError(QivcError):
    """Raised on configuration problems."""


class QivcFilterError(QivcError):
    """Raised when a filter encounters an unrecoverable error."""
