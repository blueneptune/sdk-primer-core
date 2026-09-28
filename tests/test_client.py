"""Tests for APIClient, all against a mocked transport (respx).

sleep=lambda _: None throughout: none of these tests are about retry
timing itself (see test_retry.py for that) - without it, the network-
error test below would genuinely wait through real backoff delays.
"""

import httpx
import pytest
import respx

from sdk_primer import APIClient, NetworkError, ServerError


def test_constructs_with_auth_header() -> None:
    client = APIClient(base_url="https://api.test", token="tok-123", sleep=lambda _: None)
    assert client.base_url == "https://api.test"
    assert client._http.headers["Authorization"] == "Bearer tok-123"


def test_constructs_without_token() -> None:
    client = APIClient(base_url="https://api.test", sleep=lambda _: None)
    assert "Authorization" not in client._http.headers


@respx.mock
def test_get_hits_expected_endpoint_with_params() -> None:
    client = APIClient(base_url="https://api.test", token="tok", sleep=lambda _: None)
    route = respx.get("https://api.test/users", params={"active": "true"}).mock(
        return_value=httpx.Response(200, json=[])
    )
    response = client.get("/users", params={"active": "true"})
    assert route.called
    assert response.json() == []


@respx.mock
def test_post_sends_json_body() -> None:
    client = APIClient(base_url="https://api.test", token="tok", sleep=lambda _: None)
    route = respx.post("https://api.test/users").mock(
        return_value=httpx.Response(201, json={"id": "1"})
    )
    response = client.post("/users", json={"name": "Ada"})
    assert route.called
    assert response.json() == {"id": "1"}


@respx.mock
def test_put_sends_json_body() -> None:
    client = APIClient(base_url="https://api.test", token="tok", sleep=lambda _: None)
    route = respx.put("https://api.test/users/1").mock(return_value=httpx.Response(200, json={}))
    client.put("/users/1", json={"name": "Ada 2"})
    assert route.called


@respx.mock
def test_delete_hits_expected_endpoint() -> None:
    client = APIClient(base_url="https://api.test", token="tok", sleep=lambda _: None)
    route = respx.delete("https://api.test/users/1").mock(return_value=httpx.Response(204))
    client.delete("/users/1")
    assert route.called


@respx.mock
def test_request_raises_and_logs_on_non_retryable_error(log_messages: list[str]) -> None:
    """500 is deliberately not retried (see client.py's _RETRYABLE_STATUS_CODES) -
    fails on the first attempt."""
    client = APIClient(base_url="https://api.test", token="tok", sleep=lambda _: None)
    respx.get("https://api.test/users/404").mock(return_value=httpx.Response(500))
    with pytest.raises(ServerError):
        client.get("/users/404")
    assert any("failed" in m for m in log_messages)


@respx.mock
def test_request_reraises_network_error_after_exhausting_retries(
    log_messages: list[str],
) -> None:
    client = APIClient(
        base_url="https://api.test", token="tok", max_attempts=3, sleep=lambda _: None
    )
    route = respx.get("https://api.test/widgets").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(NetworkError):
        client.get("/widgets")
    assert route.call_count == 3
    assert any("network error" in m for m in log_messages)


def test_two_clients_are_independent() -> None:
    """The whole point of this redesign: two APIClients, two different
    backends/tokens, alive at once, not interfering with each other."""
    a = APIClient(base_url="https://a.test", token="token-a", sleep=lambda _: None)
    b = APIClient(base_url="https://b.test", token="token-b", sleep=lambda _: None)
    assert a.base_url != b.base_url
    assert a._http.headers["Authorization"] != b._http.headers["Authorization"]


def test_context_manager_closes_client() -> None:
    with APIClient(base_url="https://api.test", token="tok", sleep=lambda _: None) as client:
        assert not client._http.is_closed
    assert client._http.is_closed
