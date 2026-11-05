"""Typed exceptions, so callers can catch something more specific than a
raw httpx.HTTPStatusError/HTTPError.

APIClient.request() (client.py) translates a response's status code into
one of these - once retries (chapter 17) are exhausted for a retryable
status, or immediately for anything else - and raises it instead of the
underlying httpx exception, which is still reachable via __cause__.
"""

from __future__ import annotations

from typing import Any

# Keys APIs commonly put their human-readable reason under, most
# specific first: NetBackup errorMessage, Commvault errorMessage /
# errorString, RFC 9457 detail / title, and the generic message / error.
_MESSAGE_KEYS = ("errorMessage", "errorString", "detail", "message", "error", "title")


def server_message(detail: Any) -> str | None:
    """The server's own one-line reason, from an error payload of
    whatever shape - or None if there is no obvious one."""
    if isinstance(detail, str):
        text = detail.strip()
        # A short plain-text body is the reason; an HTML error page is not.
        return text if text and len(text) <= 500 and not text.startswith("<") else None
    if isinstance(detail, dict):
        for key in _MESSAGE_KEYS:
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, (dict, list)):
                found = server_message(value)
                if found:
                    return found
        # JSON:API and similar: {"errors": [{"detail": "..."}]}
        errors = detail.get("errors")
        if isinstance(errors, list) and errors:
            return server_message(errors[0])
    if isinstance(detail, list) and detail:
        return server_message(detail[0])
    return None


class SDKError(Exception):
    """Base class for every exception sdk_primer raises for a failed
    request. Catch this to handle any API error generically.

    `status_code` is the HTTP status (None when there was no response),
    and `detail` the server's own error payload - parsed JSON if the
    response was JSON, otherwise the text - on every status, not only
    400/422: an API's 403, 404, 409 or 500 usually says why, and that
    reason is what a caller needs. Its shape varies per API, so it is
    preserved rather than normalized; `server_message` picks the
    one-line reason out of it where there is an obvious one, and it is
    also on the end of the exception's message."""

    def __init__(self, message: str, detail: Any = None, status_code: int | None = None) -> None:
        reason = server_message(detail)
        if reason and reason not in message:
            message = f"{message}: {reason[:300]}"
        super().__init__(message)
        self.detail = detail
        self.status_code = status_code

    @property
    def server_message(self) -> str | None:
        """The server's one-line reason from `detail`, if it gave one."""
        return server_message(self.detail)


class ValidationError(SDKError):
    """400 or 422: the server rejected the request body. `detail` (on
    every SDKError) carries the server's payload, usually naming the
    field it objected to."""


class AuthenticationError(SDKError):
    """401: the token is missing, invalid, or expired."""


class NotFoundError(SDKError, ValueError):
    """404. Also a ValueError - base.load()'s 404 case has raised
    ValueError since before this hierarchy existed (see the guide's
    chapter 11); this keeps that working (`except ValueError` still
    catches it) while adding the option to catch it more specifically
    (`except NotFoundError`, or broadly via `except SDKError`)."""


class RateLimitedError(SDKError):
    """429, raised only once retries (chapter 17) are exhausted - most
    429s never reach a caller at all, they're retried transparently.
    `retry_after` carries the server's Retry-After value in seconds, if
    it sent a parseable one, so a caller can decide to wait and retry at
    a higher level than APIClient's own retry loop already did."""

    def __init__(
        self,
        message: str,
        retry_after: float | None = None,
        detail: Any = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message, detail=detail, status_code=status_code)
        self.retry_after = retry_after


class ClientError(SDKError):
    """Any other 4xx not covered above (403, 405, 409, ...) - a real
    problem with the request that isn't one of the more specific,
    commonly-handled cases."""


class ServerError(SDKError):
    """5xx - a real problem on the server's end. Includes a plain 500
    immediately (never retried, see client.py) and 502/503/504 once
    retries are exhausted."""


class NetworkError(SDKError):
    """A connection never completed at all (DNS, refused, reset, timed
    out) - not a status-error case, since there was no response to read
    one from. Raised once retries (chapter 17) are exhausted; not part
    of the TODO item's original list, added for the same reason the
    status-based ones were: a caller shouldn't need to know httpx's own
    exception types to catch this generically via SDKError."""
