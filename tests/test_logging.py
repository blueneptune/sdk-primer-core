"""Tests that the logging is actually there, and that it never leaks a
full token - the one property in this module worth a real regression test
rather than just eyeballing log output.
"""

import io
from collections.abc import Callable

import httpx
import respx

from sdk_primer import APIClient, SDKError, configure_logging


def test_constructing_a_client_logs_but_redacts_token(log_messages: list[str]) -> None:
    APIClient(base_url="https://api.test", token="supersecrettoken")
    joined = "\n".join(log_messages)
    assert "supersecrettoken" not in joined
    assert "base_url=https://api.test" in joined


@respx.mock
def test_request_logs_method_and_endpoint(log_messages: list[str]) -> None:
    client = APIClient(base_url="https://api.test", token="tok")
    respx.get("https://api.test/widgets").mock(return_value=httpx.Response(200, json=[]))
    client.get("/widgets")
    joined = "\n".join(log_messages)
    assert "GET" in joined
    assert "/widgets" in joined


def test_configure_logging_changes_the_sink() -> None:
    buffer = io.StringIO()
    configure_logging(level="INFO", sink=buffer)
    from loguru import logger

    logger.info("hello from configure_logging")
    assert "hello from configure_logging" in buffer.getvalue()


def _levels_of(run: Callable[[], object]) -> list[tuple[str, str]]:
    from loguru import logger

    said: list[tuple[str, str]] = []
    sink = logger.add(
        lambda m: said.append((m.record["level"].name, m.record["message"])), level="INFO"
    )
    try:
        run()
    except SDKError:
        pass
    finally:
        logger.remove(sink)
    return said


@respx.mock
def test_a_refusal_is_the_callers_to_call_an_error() -> None:
    """A 4xx is raised to the caller and logged as information: an SDK
    that probes for a feature expects some, and logs its own error when
    a refusal really is one."""
    client = APIClient(base_url="https://api.test", token="tok", max_attempts=1)
    respx.get("https://api.test/jobs").mock(
        return_value=httpx.Response(400, json={"errorMessage": "filter is invalid"})
    )
    said = _levels_of(lambda: client.get("/jobs"))
    refused = [entry for entry in said if "/jobs" in entry[1]]
    assert refused == [
        ("INFO", "<- GET https://api.test/jobs refused with 400 - filter is invalid")
    ]


@respx.mock
def test_a_server_failure_is_still_an_error() -> None:
    client = APIClient(base_url="https://api.test", token="tok", max_attempts=1)
    respx.get("https://api.test/jobs").mock(return_value=httpx.Response(500))
    said = _levels_of(lambda: client.get("/jobs"))
    assert ("ERROR", "<- GET https://api.test/jobs failed with 500") in said
