"""Tests for the exception hierarchy and status-code translation in
client.py's _exception_for(), independent of retry behavior (test_retry.py)
or base.py's own load()-specific wrapping (test_base.py).
"""

import httpx
import pytest
import respx

from sdk_primer import (
    APIClient,
    AuthenticationError,
    ClientError,
    NetworkError,
    NotFoundError,
    RateLimitedError,
    SDKError,
    ServerError,
    ValidationError,
)


@pytest.fixture
def client() -> APIClient:
    return APIClient(base_url="https://api.test", token="tok", sleep=lambda _: None)


@pytest.mark.parametrize(
    ("status", "exc_type"),
    [
        (400, ValidationError),
        (401, AuthenticationError),
        (403, ClientError),
        (404, NotFoundError),
        (409, ClientError),
        (422, ValidationError),
        (429, RateLimitedError),
        (500, ServerError),
        (503, ServerError),
    ],
)
@respx.mock
def test_status_code_maps_to_expected_exception_type(
    client: APIClient, status: int, exc_type: type[SDKError]
) -> None:
    respx.get("https://api.test/widgets").mock(return_value=httpx.Response(status))
    with pytest.raises(exc_type):
        client.get("/widgets")


@respx.mock
def test_every_translated_exception_is_an_sdk_error(client: APIClient) -> None:
    respx.get("https://api.test/widgets").mock(return_value=httpx.Response(404))
    with pytest.raises(SDKError):
        client.get("/widgets")


@respx.mock
def test_not_found_error_is_also_a_value_error(client: APIClient) -> None:
    """base.load()'s 404 case has raised ValueError since before this
    hierarchy existed (chapter 11) - NotFoundError keeps that working."""
    respx.get("https://api.test/widgets").mock(return_value=httpx.Response(404))
    with pytest.raises(ValueError):
        client.get("/widgets")


@respx.mock
def test_translated_exception_preserves_the_original_as_cause(client: APIClient) -> None:
    respx.get("https://api.test/widgets").mock(return_value=httpx.Response(404))
    try:
        client.get("/widgets")
    except NotFoundError as exc:
        assert isinstance(exc.__cause__, httpx.HTTPStatusError)
    else:
        pytest.fail("expected NotFoundError")


@respx.mock
def test_validation_error_carries_json_detail_from_the_server(client: APIClient) -> None:
    respx.post("https://api.test/widgets").mock(
        return_value=httpx.Response(400, json={"errors": {"name": "required"}})
    )
    with pytest.raises(ValidationError) as exc_info:
        client.post("/widgets", json={})
    assert exc_info.value.detail == {"errors": {"name": "required"}}


@respx.mock
def test_validation_error_falls_back_to_text_when_body_isnt_json(client: APIClient) -> None:
    respx.post("https://api.test/widgets").mock(return_value=httpx.Response(422, text="not json"))
    with pytest.raises(ValidationError) as exc_info:
        client.post("/widgets", json={})
    assert exc_info.value.detail == "not json"


@respx.mock
def test_rate_limited_error_carries_retry_after(client: APIClient) -> None:
    respx.get("https://api.test/widgets").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "3"})
    )
    with pytest.raises(RateLimitedError) as exc_info:
        client.get("/widgets")
    assert exc_info.value.retry_after == 3.0


@respx.mock
def test_rate_limited_error_without_retry_after_header(client: APIClient) -> None:
    respx.get("https://api.test/widgets").mock(return_value=httpx.Response(429))
    with pytest.raises(RateLimitedError) as exc_info:
        client.get("/widgets")
    assert exc_info.value.retry_after is None


@respx.mock
def test_network_error_is_an_sdk_error_with_the_original_as_cause(client: APIClient) -> None:
    respx.get("https://api.test/widgets").mock(side_effect=httpx.ConnectError("boom"))
    try:
        client.get("/widgets")
    except NetworkError as exc:
        assert isinstance(exc.__cause__, httpx.ConnectError)
    else:
        pytest.fail("expected NetworkError")
