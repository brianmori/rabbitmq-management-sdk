class TransportError(Exception):
    """Base exception for all transport errors."""


class TransportTimeoutError(TransportError):
    """Request timed out."""


class TransportConnectionError(TransportError):
    """Network or connection failure."""


class TransportResponseError(TransportError):
    """Unexpected HTTP error."""

    def __init__(self, message: str, status_code: int | None = None, response_body: bytes | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body