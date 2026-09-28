"""Tests for the per-request correlation id APIClient.request() binds via
loguru's logger.bind() (TODO item 11).

log_messages (sdk_primer.testing) only captures formatted text, not the
extra fields a bound logger attaches - these tests need the underlying
loguru record itself, so they add their own sink instead.
"""

from collections.abc import Generator

import httpx
import pytest
import respx
from loguru import logger

from sdk_primer import APIClient, ServerError


@pytest.fixture
def request_ids() -> Generator[list[str | None]]:
    """Every log record's extra["request_id"] emitted during a test, in
    order - None for a record nothing bound one to."""
    ids: list[str | None] = []
    handler_id = logger.add(
        lambda message: ids.append(message.record["extra"]["request_id"]),
        level="TRACE",
    )
    yield ids
    logger.remove(handler_id)


@respx.mock
def test_every_retry_of_one_call_shares_the_same_request_id(
    request_ids: list[str | None],
) -> None:
    client = APIClient(base_url="https://api.test", token="tok", sleep=lambda _: None)
    respx.get("https://api.test/widgets").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=[])]
    )

    client.get("/widgets")

    # -> attempt 1, <- retrying, -> attempt 2, <- 200: four request()
    # log lines, all sharing one id (APIClient.__init__ logs too, with no
    # request_id bound - filtered out here, not what this test is about).
    bound = [i for i in request_ids if i is not None]
    assert len(bound) == 4
    assert len(set(bound)) == 1


@respx.mock
def test_two_separate_calls_get_different_request_ids(request_ids: list[str | None]) -> None:
    client = APIClient(base_url="https://api.test", token="tok", sleep=lambda _: None)
    respx.get("https://api.test/widgets").mock(return_value=httpx.Response(200, json=[]))

    client.get("/widgets")
    client.get("/widgets")

    ids_seen = {i for i in request_ids if i is not None}
    assert len(ids_seen) == 2


@respx.mock
def test_request_id_is_bound_on_the_final_error_log_too(request_ids: list[str | None]) -> None:
    client = APIClient(
        base_url="https://api.test", token="tok", max_attempts=1, sleep=lambda _: None
    )
    respx.get("https://api.test/widgets").mock(return_value=httpx.Response(500))

    with pytest.raises(ServerError):
        client.get("/widgets")

    bound = [i for i in request_ids if i is not None]
    assert len(bound) == 2  # -> attempt 1, <- failed
    assert len(set(bound)) == 1


def test_a_log_line_outside_request_has_no_request_id_bound(
    request_ids: list[str | None],
) -> None:
    """APIClient.__init__ logs but isn't a request() call - proves the
    default configured in _logging.py (extra={"request_id": None}) is
    what stops that log line from KeyError-ing against a format
    referencing extra["request_id"], rather than every log line
    accidentally getting one bound."""
    APIClient(base_url="https://api.test", token="tok", sleep=lambda _: None)

    assert request_ids == [None]
