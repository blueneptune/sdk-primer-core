"""Tests that the logging is actually there, and that it never leaks a
full token - the one property in this module worth a real regression test
rather than just eyeballing log output.
"""

import io

import httpx
import respx

from sdk_primer import APIClient, configure_logging


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
