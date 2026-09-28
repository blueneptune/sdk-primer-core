"""Tests for retry/backoff and idempotency keys in APIClient.request().

Every client here injects its own sleep spy (a list-appending fake, not
the shared api_client fixture's no-op) specifically to assert on retry
counts and delays - that's the whole subject of this file.
"""

import httpx
import pytest
import respx

from sdk_primer import (
    APIClient,
    NotFoundError,
    RateLimitedError,
    ServerError,
)


def _client(**kwargs: object) -> tuple[APIClient, list[float]]:
    delays: list[float] = []
    client = APIClient(
        base_url="https://api.test",
        token="tok",
        sleep=delays.append,
        **kwargs,  # type: ignore[arg-type]
    )
    return client, delays


@respx.mock
def test_retries_503_and_eventually_succeeds() -> None:
    client, delays = _client(max_attempts=3)
    route = respx.get("https://api.test/widgets").mock(
        side_effect=[httpx.Response(503), httpx.Response(503), httpx.Response(200, json=[])]
    )
    response = client.get("/widgets")
    assert response.status_code == 200
    assert route.call_count == 3
    assert len(delays) == 2


@respx.mock
def test_exhausts_retries_and_raises() -> None:
    client, delays = _client(max_attempts=3)
    route = respx.get("https://api.test/widgets").mock(return_value=httpx.Response(503))
    with pytest.raises(ServerError):
        client.get("/widgets")
    assert route.call_count == 3
    assert len(delays) == 2


@respx.mock
def test_does_not_retry_a_plain_4xx() -> None:
    client, delays = _client(max_attempts=3)
    route = respx.get("https://api.test/widgets/missing").mock(return_value=httpx.Response(404))
    with pytest.raises(NotFoundError):
        client.get("/widgets/missing")
    assert route.call_count == 1
    assert delays == []


@respx.mock
def test_does_not_retry_a_plain_500() -> None:
    """500 usually means a real bug on the server, not a transient
    condition - deliberately excluded from _RETRYABLE_STATUS_CODES."""
    client, delays = _client(max_attempts=3)
    route = respx.get("https://api.test/widgets").mock(return_value=httpx.Response(500))
    with pytest.raises(ServerError):
        client.get("/widgets")
    assert route.call_count == 1
    assert delays == []


@respx.mock
def test_retries_network_errors() -> None:
    client, delays = _client(max_attempts=3)
    route = respx.get("https://api.test/widgets").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json=[])]
    )
    response = client.get("/widgets")
    assert response.status_code == 200
    assert route.call_count == 2
    assert len(delays) == 1


@respx.mock
def test_backoff_delay_grows_and_stays_within_the_jittered_cap() -> None:
    client, delays = _client(max_attempts=3, backoff_base=0.5)
    respx.get("https://api.test/widgets").mock(return_value=httpx.Response(503))
    with pytest.raises(ServerError):
        client.get("/widgets")
    assert len(delays) == 2
    # full jitter: each delay is in [0, base * 2**attempt]
    assert 0 <= delays[0] <= 0.5 * 2**1
    assert 0 <= delays[1] <= 0.5 * 2**2


@respx.mock
def test_retry_after_header_is_respected_over_backoff() -> None:
    client, delays = _client(max_attempts=2, backoff_base=100.0)  # backoff would be huge if used
    respx.get("https://api.test/widgets").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "1.5"})
    )
    with pytest.raises(RateLimitedError) as exc_info:
        client.get("/widgets")
    assert delays == [1.5]
    assert exc_info.value.retry_after == 1.5


@respx.mock
def test_retry_after_falls_back_to_backoff_when_not_plain_seconds() -> None:
    """RFC 7231 also allows an HTTP-date form (e.g. "Wed, 21 Oct ...") -
    not worth parsing here (rare in practice for a JSON API), so an
    unparseable Retry-After just falls back to normal backoff instead of
    crashing."""
    client, delays = _client(max_attempts=2, backoff_base=0.5)
    respx.get("https://api.test/widgets").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    )
    with pytest.raises(RateLimitedError) as exc_info:
        client.get("/widgets")
    assert len(delays) == 1
    assert 0 <= delays[0] <= 0.5 * 2**1
    assert exc_info.value.retry_after is None


@respx.mock
def test_max_attempts_one_disables_retry() -> None:
    client, delays = _client(max_attempts=1)
    route = respx.get("https://api.test/widgets").mock(return_value=httpx.Response(503))
    with pytest.raises(ServerError):
        client.get("/widgets")
    assert route.call_count == 1
    assert delays == []


@respx.mock
def test_post_generates_an_idempotency_key_by_default() -> None:
    client, _ = _client()
    route = respx.post("https://api.test/widgets").mock(
        return_value=httpx.Response(201, json={"id": "w1"})
    )
    client.post("/widgets", json={"name": "Gadget"})
    key = route.calls.last.request.headers.get("Idempotency-Key")
    assert key
    assert len(key) == 32  # uuid4().hex


@respx.mock
def test_post_honors_a_caller_supplied_idempotency_key() -> None:
    client, _ = _client()
    route = respx.post("https://api.test/widgets").mock(
        return_value=httpx.Response(201, json={"id": "w1"})
    )
    client.post("/widgets", json={"name": "Gadget"}, idempotency_key="my-key-123")
    assert route.calls.last.request.headers.get("Idempotency-Key") == "my-key-123"


@respx.mock
def test_post_reuses_the_same_idempotency_key_across_retries() -> None:
    client, _ = _client(max_attempts=3)
    route = respx.post("https://api.test/widgets").mock(
        side_effect=[httpx.Response(503), httpx.Response(201, json={"id": "w1"})]
    )
    client.post("/widgets", json={"name": "Gadget"})
    assert route.call_count == 2
    key_1 = route.calls[0].request.headers.get("Idempotency-Key")
    key_2 = route.calls[1].request.headers.get("Idempotency-Key")
    assert key_1 == key_2


@respx.mock
def test_get_does_not_get_an_idempotency_key() -> None:
    client, _ = _client()
    route = respx.get("https://api.test/widgets").mock(return_value=httpx.Response(200, json=[]))
    client.get("/widgets")
    assert "Idempotency-Key" not in route.calls.last.request.headers


@respx.mock
def test_idempotency_header_name_is_configurable() -> None:
    client, _ = _client(idempotency_header="X-Request-Id")
    route = respx.post("https://api.test/widgets").mock(
        return_value=httpx.Response(201, json={"id": "w1"})
    )
    client.post("/widgets", json={"name": "Gadget"})
    assert route.calls.last.request.headers.get("X-Request-Id")
    assert "Idempotency-Key" not in route.calls.last.request.headers
