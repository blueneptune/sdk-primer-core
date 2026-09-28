"""Pytest fixtures for testing SDKs built on sdk_primer.

Registered as a pytest plugin (see pyproject.toml's
[project.entry-points.pytest11]) - having sdk_primer installed is enough
to get these fixtures in any project's tests, no conftest.py wiring
needed. sdk-primer's own tests use them too (see tests/), which is the
proof they actually work this way and not just via a local import.

A subpackage, not a flat module, for the same reason example/ (one
directory up) is one: this is a third thing, neither the reusable engine
(base.py/client.py/resource.py) nor the worked example - optional dev/
test tooling, behind its own [testing] extra - and the directory tree
should say so rather than relying on the name alone. The entry point
above resolves to this __init__.py exactly as it would a flat testing.py,
so nothing else needed to change.
"""

from collections.abc import Generator

import pytest

from ..client import APIClient


@pytest.fixture
def api_client() -> APIClient:
    """A throwaway APIClient pointed at a fake host, ready for respx to
    intercept. https://api.test is IANA's reserved test TLD (RFC 2606) -
    never a real, routable host - so a test that forgets to mock a route
    fails loudly instead of accidentally reaching the network.

    sleep=lambda _: None: retries (see client.py) still happen, but don't
    actually wait real backoff delays - a test exercising retry behavior
    itself should build its own APIClient with a spy in place of sleep to
    assert on the delays, rather than relying on this default.
    """
    return APIClient(base_url="https://api.test", token="test-token", sleep=lambda _: None)


@pytest.fixture
def log_messages() -> Generator[list[str]]:
    """Capture every loguru message emitted during a test, formatted text only."""
    from loguru import logger

    messages: list[str] = []
    handler_id = logger.add(messages.append, level="TRACE", format="{message}")
    yield messages
    logger.remove(handler_id)
