"""Tests for pluggable authentication: APIClient's auth=/default_headers=
options, and TokenExchangeAuth.
"""

import httpx
import pytest
import respx

from sdk_primer import APIClient, TokenExchangeAuth


def test_token_and_auth_together_is_rejected() -> None:
    with pytest.raises(ValueError, match="either token= or auth="):
        APIClient(base_url="https://api.test", token="tok", auth=httpx.BasicAuth("u", "p"))


@respx.mock
def test_default_headers_are_sent_on_every_request() -> None:
    client = APIClient(
        base_url="https://api.test",
        default_headers={"X-Api-Key": "static-key-123"},
        sleep=lambda _: None,
    )
    route = respx.get("https://api.test/widgets").mock(return_value=httpx.Response(200, json=[]))
    client.get("/widgets")
    assert route.calls.last.request.headers.get("X-Api-Key") == "static-key-123"


@respx.mock
def test_auth_parameter_accepts_any_httpx_auth() -> None:
    """Proves the escape hatch works for an arbitrary httpx.Auth, not
    just TokenExchangeAuth specifically - here, plain Basic auth."""
    client = APIClient(
        base_url="https://api.test", auth=httpx.BasicAuth("myuser", "mypass"), sleep=lambda _: None
    )
    route = respx.get("https://api.test/widgets").mock(return_value=httpx.Response(200, json=[]))
    client.get("/widgets")
    auth_header = route.calls.last.request.headers.get("Authorization")
    assert auth_header is not None
    assert auth_header.startswith("Basic ")


# --- TokenExchangeAuth -------------------------------------------------


@respx.mock
def test_exchanges_credentials_and_attaches_bearer_token() -> None:
    respx.post("https://auth.test/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-abc"})
    )
    auth = TokenExchangeAuth(
        token_url="https://auth.test/token", credentials={"client_id": "x", "client_secret": "y"}
    )
    client = APIClient(base_url="https://api.test", auth=auth, sleep=lambda _: None)
    route = respx.get("https://api.test/widgets").mock(return_value=httpx.Response(200, json=[]))

    client.get("/widgets")

    assert route.calls.last.request.headers.get("Authorization") == "Bearer tok-abc"


@respx.mock
def test_only_exchanges_once_across_multiple_requests() -> None:
    token_route = respx.post("https://auth.test/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-abc"})
    )
    auth = TokenExchangeAuth(
        token_url="https://auth.test/token", credentials={"client_id": "x", "client_secret": "y"}
    )
    client = APIClient(base_url="https://api.test", auth=auth, sleep=lambda _: None)
    respx.get("https://api.test/widgets").mock(return_value=httpx.Response(200, json=[]))

    client.get("/widgets")
    client.get("/widgets")
    client.get("/widgets")

    assert token_route.call_count == 1


@respx.mock
def test_custom_token_from_response_field_name() -> None:
    """Matches NetBackup's shape from the docstring: the token lives
    under a field the default guesses don't cover."""
    respx.post("https://netbackup.test/login").mock(
        return_value=httpx.Response(200, json={"token": "nbu-session-token"})
    )
    auth = TokenExchangeAuth(
        token_url="https://netbackup.test/login",
        credentials={"userName": "svc", "password": "secret"},
        token_from_response=lambda r: r.json()["token"],
    )
    client = APIClient(base_url="https://api.test", auth=auth, sleep=lambda _: None)
    respx.get("https://api.test/widgets").mock(return_value=httpx.Response(200, json=[]))

    client.get("/widgets")

    token_request = respx.calls[0].request
    assert b"svc" in token_request.content


def test_default_token_from_response_raises_a_helpful_error_on_unknown_shape() -> None:
    from sdk_primer.auth import _default_token_from_response

    response = httpx.Response(200, json={"something_else": "value"})
    with pytest.raises(KeyError, match="token_from_response"):
        _default_token_from_response(response)


@respx.mock
def test_re_exchanges_and_retries_once_on_401() -> None:
    token_route = respx.post("https://auth.test/token").mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "expired-token"}),
            httpx.Response(200, json={"access_token": "fresh-token"}),
        ]
    )
    auth = TokenExchangeAuth(
        token_url="https://auth.test/token", credentials={"client_id": "x", "client_secret": "y"}
    )
    client = APIClient(base_url="https://api.test", auth=auth, sleep=lambda _: None)
    widgets_route = respx.get("https://api.test/widgets").mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json=[])]
    )

    response = client.get("/widgets")

    assert response.status_code == 200
    assert token_route.call_count == 2
    assert widgets_route.call_count == 2
    assert widgets_route.calls[1].request.headers.get("Authorization") == "Bearer fresh-token"


@respx.mock
def test_re_exchanges_once_ttl_has_elapsed() -> None:
    token_route = respx.post("https://auth.test/token").mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "token-1"}),
            httpx.Response(200, json={"access_token": "token-2"}),
        ]
    )
    clock = iter([0.0, 100.0, 100.0])  # 1st fetch, 2nd check (expired), 2nd fetch
    auth = TokenExchangeAuth(
        token_url="https://auth.test/token",
        credentials={"client_id": "x", "client_secret": "y"},
        ttl_seconds=60.0,
        now=lambda: next(clock),
    )
    client = APIClient(base_url="https://api.test", auth=auth, sleep=lambda _: None)
    respx.get("https://api.test/widgets").mock(return_value=httpx.Response(200, json=[]))

    client.get("/widgets")
    client.get("/widgets")

    assert token_route.call_count == 2


@respx.mock
def test_auth_scheme_none_sends_raw_token_without_prefix() -> None:
    respx.post("https://auth.test/token").mock(
        return_value=httpx.Response(200, json={"access_token": "raw-token-value"})
    )
    auth = TokenExchangeAuth(
        token_url="https://auth.test/token",
        credentials={},
        auth_scheme=None,
    )
    client = APIClient(base_url="https://api.test", auth=auth, sleep=lambda _: None)
    respx.get("https://api.test/widgets").mock(return_value=httpx.Response(200, json=[]))

    client.get("/widgets")

    assert respx.calls[-1].request.headers.get("Authorization") == "raw-token-value"


@respx.mock
def test_custom_auth_header_name() -> None:
    respx.post("https://auth.test/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-abc"})
    )
    auth = TokenExchangeAuth(
        token_url="https://auth.test/token",
        credentials={},
        auth_header="X-Session-Token",
        auth_scheme=None,
    )
    client = APIClient(base_url="https://api.test", auth=auth, sleep=lambda _: None)
    respx.get("https://api.test/widgets").mock(return_value=httpx.Response(200, json=[]))

    client.get("/widgets")

    assert respx.calls[-1].request.headers.get("X-Session-Token") == "tok-abc"
