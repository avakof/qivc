class QivcError(Exception):
    """Base exception for all QIVC errors."""


class QivcDataError(QivcError):
    """Raised when a data agent fails to fetch or parse data."""


class QivcConfigError(QivcError):
    """Raised on configuration problems."""


class QivcFilterError(QivcError):
    """Raised when a filter encounters an unrecoverable error."""
