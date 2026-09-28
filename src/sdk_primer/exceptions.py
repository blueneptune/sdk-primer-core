"""Typed exceptions, so callers can catch something more specific than a
raw httpx.HTTPStatusError/HTTPError.

APIClient.request() (client.py) translates a response's status code into
one of these - once retries (chapter 17) are exhausted for a retryable
status, or immediately for anything else - and raises it instead of the
underlying httpx exception, which is still reachable via __cause__.
"""

from __future__ import annotations

from typing import Any


class SDKError(Exception):
    """Base class for every exception sdk_primer raises for a failed
    request. Catch this to handle any API error generically."""


class ValidationError(SDKError):
    """400 or 422: the server rejected the request body. `detail` carries
    whatever the server's own error payload was (parsed JSON if the
    response was JSON, otherwise the raw text) - shape varies per API,
    so this doesn't try to normalize it, just preserves it."""

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.detail = detail


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

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
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
