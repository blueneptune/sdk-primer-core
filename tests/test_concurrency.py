"""Demonstrates sharing one APIClient safely across a ThreadPoolExecutor:
httpx.Client is documented as thread-safe, so one APIClient can fire off
many requests concurrently - e.g. posting a batch of 200+ events - without
needing an async SDK. Each thread blocks on network I/O, not CPU, so this
is a genuine concurrency win despite the GIL.
"""

from concurrent.futures import ThreadPoolExecutor
from itertools import count
from typing import ClassVar

import httpx
import respx

from sdk_primer import APIClient
from sdk_primer.base import BaseAPIModel
from sdk_primer.resource import ResourceManager


class Event(BaseAPIModel):
    name: str
    _resource_path: ClassVar[str] = "events"


@respx.mock
def test_many_concurrent_creates_from_a_thread_pool(api_client: APIClient) -> None:
    next_id = count(1)
    respx.post("https://api.test/events").mock(
        side_effect=lambda request: httpx.Response(201, json={"id": f"e{next(next_id)}"})
    )

    events = ResourceManager(Event, api_client)

    def create_one(i: int) -> str:
        event = events.create(name=f"Event {i}")
        assert event.id is not None
        return event.id

    record_count = 200
    with ThreadPoolExecutor(max_workers=20) as executor:
        created_ids = list(executor.map(create_one, range(record_count)))

    # Every call got back an id, and no two threads stepped on each other's
    # instance state (a race that made two calls share/overwrite the same
    # id would show up here as a duplicate).
    assert len(created_ids) == record_count
    assert len(set(created_ids)) == record_count
