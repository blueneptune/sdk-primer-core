"""The transport layer: one class, APIClient, that knows how to talk to one
backend (base URL, auth token, timeout, retry policy).

This used to be module-level globals (set_credentials() + get/post/put/
delete functions) - see the guide's chapter on that redesign for why that
changed. The short version: a module can only hold one configured backend
at a time, and a template meant to front several different services at
once (possibly the same service twice, e.g. two connections to different
servers) needs more than one. Constructing an APIClient explicitly, and
handing it to whatever needs it (see base.py, resource.py), means any
number of independently-configured clients can coexist in one process
without interfering with each other.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterator
from typing import Any
from uuid import uuid4

import httpx
from loguru import logger

from .exceptions import (
    AuthenticationError,
    ClientError,
    NetworkError,
    NotFoundError,
    RateLimitedError,
    SDKError,
    ServerError,
    ValidationError,
)
from .pagination import PaginationStrategy

# Status codes worth retrying: gateway/availability signals, not client
# mistakes. Deliberately excludes plain 500 - a generic server error
# usually means a real bug on the other end, and retrying is unlikely to
# help (and can mask the problem) the way it can for a proxy briefly
# unable to reach a backend (502/503/504) or a rate limit (429).
_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})

# Status codes with their own exception type. Anything not listed here
# falls back to ServerError (>=500) or ClientError (other 4xx) - see
# _exception_for(). 400 and 422 both mean "the server rejected the
# request body" in different APIs' conventions; both map to
# ValidationError rather than picking just one.
_EXCEPTION_FOR_STATUS: dict[int, type[SDKError]] = {
    400: ValidationError,
    401: AuthenticationError,
    404: NotFoundError,
    422: ValidationError,
    429: RateLimitedError,
}


def _exception_for(response: httpx.Response) -> SDKError:
    """Translate a failed response into the matching SDKError subclass."""
    exc_type = _EXCEPTION_FOR_STATUS.get(response.status_code)
    if exc_type is None:
        exc_type = ServerError if response.status_code >= 500 else ClientError
    message = f"{response.request.method} {response.request.url} -> {response.status_code}"

    if exc_type is RateLimitedError:
        retry_after = response.headers.get("Retry-After")
        parsed: float | None = None
        if retry_after is not None:
            try:
                parsed = float(retry_after)
            except ValueError:
                pass
        return RateLimitedError(message, retry_after=parsed)

    if exc_type is ValidationError:
        try:
            detail: Any = response.json()
        except ValueError:
            detail = response.text
        return ValidationError(message, detail=detail)

    return exc_type(message)


class APIClient:
    """Everything needed to reach one backend, bundled into one object.

    Construct one of these per backend you talk to. httpx.Client is
    documented as thread-safe, so one APIClient can also be shared across a
    ThreadPoolExecutor to fire off many requests concurrently - see
    tests/test_concurrency.py.
    """

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        auth: httpx.Auth | None = None,
        default_headers: dict[str, str] | None = None,
        timeout: float = 10.0,
        max_attempts: int = 3,
        backoff_base: float = 0.5,
        idempotency_header: str = "Idempotency-Key",
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if token is not None and auth is not None:
            raise ValueError("Pass either token= or auth=, not both.")
        self.base_url = base_url
        self.token = token
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.idempotency_header = idempotency_header
        # Injectable, same reasoning as `transport` below: tests need to
        # not actually wait through real backoff delays. Defaults to the
        # real time.sleep for normal use.
        self._sleep = sleep
        # default_headers covers a static "always send this exact header"
        # scheme (e.g. NetBackup's API-key mode) without needing an Auth
        # subclass for something that simple. token= is the shorthand for
        # the common static-Bearer-token case; auth= (see auth.py) is the
        # escape hatch for anything dynamic - a token-exchange flow,
        # request signing, whatever a real service actually needs.
        headers = dict(default_headers) if default_headers else {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        # transport normally stays None and httpx opens real sockets.
        # Passing one in is for testing against an in-process ASGI app
        # (httpx.ASGITransport(app=fastapi_app)) without a real server on a
        # real port - same request/response path through FastAPI, no
        # sockets, much faster than examples/user-service's background-
        # thread approach. See tests/test_client.py.
        self._http = httpx.Client(
            base_url=base_url, headers=headers, auth=auth, timeout=timeout, transport=transport
        )
        redacted = f"{token[:4]}…" if token and len(token) > 4 else "…"
        logger.info(
            "Initialized APIClient: base_url={} token={} auth={} timeout={} max_attempts={}",
            base_url,
            redacted if token else None,
            type(auth).__name__ if auth else None,
            timeout,
            max_attempts,
        )

    def close(self) -> None:
        logger.debug("Closing APIClient for {}", self.base_url)
        self._http.close()

    def __enter__(self) -> APIClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _retry_delay(self, attempt: int, response: httpx.Response | None) -> float:
        """Seconds to wait before the next attempt.

        Prefers a 429 response's own Retry-After header (only the
        plain-seconds form - not the HTTP-date form RFC 7231 also allows,
        which is rare in practice for a JSON API and not worth the extra
        parsing here) over guessing; falls back to exponential backoff
        with full jitter (AWS's recommended shape: a random delay between
        0 and the cap, not the cap itself, so many clients retrying at
        once don't all land on the same instant).
        """
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    return float(retry_after)
                except ValueError:
                    pass
        cap = self.backoff_base * (2**attempt)
        return random.uniform(0, cap)

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        idempotency_key: str | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Issue one request, retrying transient failures, logging every
        attempt, and re-raising once max_attempts is exhausted.

        idempotency_key only matters for POST (GET/PUT/DELETE are already
        idempotent by HTTP semantics - repeating one has the same effect,
        so retrying them safely needs nothing extra). A POST retried
        without one risks creating a duplicate resource if the original
        request actually reached the server and only the *response* was
        lost - the key lets the server recognize a retry of the same
        logical call instead of a second, separate one. Generated once
        per call (not per attempt) here if the caller didn't supply one,
        so every retry of the same POST reuses the same key.
        """
        if method == "POST":
            headers = kwargs.pop("headers", None) or {}
            headers[self.idempotency_header] = idempotency_key or uuid4().hex
            kwargs["headers"] = headers

        # Bound once per logical call, reused across every retry attempt
        # below - so grepping one request_id shows every log line for one
        # request, retries included, not just its final outcome. See
        # TODO item 11 and the guide's chapter on it.
        log = logger.bind(request_id=uuid4().hex)

        attempt = 1
        while True:
            log.debug(
                "-> {} {}{} (attempt {}/{})",
                method,
                self.base_url,
                endpoint,
                attempt,
                self.max_attempts,
            )
            try:
                response = self._http.request(method, endpoint, **kwargs)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                retryable = exc.response.status_code in _RETRYABLE_STATUS_CODES
                if retryable and attempt < self.max_attempts:
                    delay = self._retry_delay(attempt, exc.response)
                    log.warning(
                        "<- {} {}{} failed with {} (attempt {}/{}), retrying in {:.2f}s",
                        method,
                        self.base_url,
                        endpoint,
                        exc.response.status_code,
                        attempt,
                        self.max_attempts,
                        delay,
                    )
                    self._sleep(delay)
                    attempt += 1
                    continue
                log.error("<- {} {}{} failed: {}", method, self.base_url, endpoint, exc)
                raise _exception_for(exc.response) from exc
            except httpx.HTTPError as exc:
                if attempt < self.max_attempts:
                    delay = self._retry_delay(attempt, None)
                    log.warning(
                        "<- {} {}{} network error (attempt {}/{}): {} - retrying in {:.2f}s",
                        method,
                        self.base_url,
                        endpoint,
                        attempt,
                        self.max_attempts,
                        exc,
                        delay,
                    )
                    self._sleep(delay)
                    attempt += 1
                    continue
                log.error("<- {} {}{} network error: {}", method, self.base_url, endpoint, exc)
                raise NetworkError(f"{method} {self.base_url}{endpoint}: {exc}") from exc
            log.debug("<- {} {}{} {}", method, self.base_url, endpoint, response.status_code)
            return response

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> httpx.Response:
        return self.request("GET", endpoint, params=params)

    def paginate(
        self,
        endpoint: str,
        *,
        pagination: PaginationStrategy,
        params: dict[str, Any] | None = None,
        items_from_response: Callable[[httpx.Response], list[Any]] = lambda r: r.json(),
        max_pages: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yields raw JSON items across pages, walking `pagination` (see
        pagination.py) until it says there's nothing more - or max_pages
        is hit, a safety cap against a misconfigured or misbehaving
        server looping forever, since nothing else here can tell
        "genuinely more data" apart from "a cursor that never advances".

        Each page fetch goes through get() -> request(), so retry/backoff
        (chapter 17) and exception translation (chapter 20) both apply to
        every page automatically, same as any other call.

        items_from_response defaults to assuming the response body itself
        is a JSON array. Many real APIs wrap results in an envelope
        instead - ServiceNow's Table API returns {"result": [...]}, for
        example - pass items_from_response=lambda r: r.json()["result"]
        (or whatever the target service's envelope key is) for those.
        """
        page_number = 0
        previous_response: httpx.Response | None = None
        while True:
            if max_pages is not None and page_number >= max_pages:
                logger.warning("paginate({}): stopped at max_pages={}", endpoint, max_pages)
                return
            page_params = {
                **(params or {}),
                **pagination.params_for_page(page_number, previous_response),
            }
            response = self.get(endpoint, params=page_params)
            items = items_from_response(response)
            if not isinstance(items, list):
                raise TypeError(
                    f"items_from_response returned {type(items).__name__}, expected a "
                    f"list - if {endpoint} wraps its results in an envelope (e.g. "
                    f'{{"result": [...]}}), pass items_from_response=lambda r: '
                    f"r.json()['result']"
                )
            yield from items
            if not pagination.has_next_page(response, items):
                return
            previous_response = response
            page_number += 1

    def post(
        self,
        endpoint: str,
        json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> httpx.Response:
        return self.request("POST", endpoint, json=json, idempotency_key=idempotency_key)

    def put(self, endpoint: str, json: dict[str, Any] | None = None) -> httpx.Response:
        return self.request("PUT", endpoint, json=json)

    def delete(self, endpoint: str) -> httpx.Response:
        return self.request("DELETE", endpoint)
