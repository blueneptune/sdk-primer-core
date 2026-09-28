"""Pluggable authentication, built on httpx's own Auth interface.

APIClient(auth=...) accepts any httpx.Auth instance - the escape hatch
for anything beyond the simple static-token case APIClient(token=...)
already covers. httpx re-runs an Auth's auth_flow() per request
(including per retry attempt - it composes with chapter 17's retry loop
for free) and can inspect the response to react to a 401, which is what
TokenExchangeAuth below uses to re-authenticate transparently.

TokenExchangeAuth implements one common, reusable shape: POST some
credentials to a token endpoint once, extract a bearer token from the
response, cache it, and re-exchange it on expiry or a 401. Two real
services already need exactly this, with different payload/response
shapes:

    # Rubrik: client_id/client_secret POSTed to a token endpoint
    # (OAuth2 client-credentials shape).
    auth = TokenExchangeAuth(
        token_url="https://rubrik.example.com/api/client_token",
        credentials={"client_id": "...", "client_secret": "..."},
    )

    # NetBackup: POST credentials to a login endpoint, get a session
    # token back under a different field name than the default guesses.
    auth = TokenExchangeAuth(
        token_url="https://netbackup.example.com/login",
        credentials={"userName": "...", "password": "..."},
        token_from_response=lambda r: r.json()["token"],
    )

    client = APIClient(base_url="...", auth=auth)

Configuring TokenExchangeAuth for a new service is a difference in
*arguments*, not a difference in *class* - see token_from_response and
auth_scheme below for the two things that vary most between APIs.

NetBackup's other mechanism - a static API key sent as part of a header,
no exchange involved - doesn't need any of this: see APIClient's own
`default_headers` parameter instead.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Generator
from typing import Any

import httpx
from loguru import logger


def _default_token_from_response(response: httpx.Response) -> str:
    """Looks for the token under the field names REST APIs most commonly
    use. Pass your own token_from_response= to TokenExchangeAuth if your
    service uses something else - this is a starting guess, not a
    standard."""
    body = response.json()
    for key in ("access_token", "token", "session_token"):
        if key in body:
            return str(body[key])
    raise KeyError(
        f"Couldn't find a token in the response body under access_token, "
        f"token, or session_token - pass token_from_response= to "
        f"TokenExchangeAuth for this API's actual field name. Body: {body!r}"
    )


class TokenExchangeAuth(httpx.Auth):
    """POST credentials to token_url once, cache the token, attach it to
    every request, and transparently re-exchange it on a 401 or once
    ttl_seconds has elapsed since the last exchange.

    The exchange request itself doesn't get APIClient's retry/backoff
    (chapter 17) - it uses a plain httpx.Client, injectable via
    `client=` for testing. Worth building if this becomes a real gap in
    practice, not assumed needed up front.
    """

    def __init__(
        self,
        token_url: str,
        credentials: dict[str, Any],
        token_from_response: Callable[[httpx.Response], str] = _default_token_from_response,
        auth_scheme: str | None = "Bearer",
        auth_header: str = "Authorization",
        ttl_seconds: float | None = None,
        client: httpx.Client | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.token_url = token_url
        self.credentials = credentials
        self.token_from_response = token_from_response
        self.auth_scheme = auth_scheme
        self.auth_header = auth_header
        self.ttl_seconds = ttl_seconds
        self._exchange_client = client or httpx.Client()
        self._now = now
        self._token: str | None = None
        self._fetched_at: float | None = None

    def _needs_exchange(self) -> bool:
        if self._token is None:
            return True
        if self.ttl_seconds is not None and self._fetched_at is not None:
            return (self._now() - self._fetched_at) >= self.ttl_seconds
        return False

    def _exchange(self) -> None:
        logger.info("TokenExchangeAuth: exchanging credentials at {}", self.token_url)
        response = self._exchange_client.post(self.token_url, json=self.credentials)
        response.raise_for_status()
        self._token = self.token_from_response(response)
        self._fetched_at = self._now()

    def _apply(self, request: httpx.Request) -> None:
        value = f"{self.auth_scheme} {self._token}" if self.auth_scheme else self._token
        request.headers[self.auth_header] = value or ""

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response]:
        if self._needs_exchange():
            self._exchange()
        self._apply(request)
        response = yield request

        if response.status_code == 401:
            logger.warning("TokenExchangeAuth: got 401, re-exchanging and retrying once")
            self._exchange()
            self._apply(request)
            yield request
