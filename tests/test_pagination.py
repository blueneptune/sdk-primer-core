"""Tests for OffsetPagination, CursorPagination, APIClient.paginate(),
and BaseAPIModel.find_iter().
"""

from typing import ClassVar

import httpx
import pytest
import respx

from sdk_primer import APIClient, CursorPagination, OffsetPagination
from sdk_primer.base import BaseAPIModel


class Item(BaseAPIModel):
    name: str
    _resource_path: ClassVar[str] = "items"


# --- OffsetPagination / CursorPagination in isolation -------------------


def test_offset_pagination_params_for_page() -> None:
    strategy = OffsetPagination(page_size=25)
    assert strategy.params_for_page(0, None) == {"page": 1, "page_size": 25}
    assert strategy.params_for_page(2, None) == {"page": 3, "page_size": 25}


def test_offset_pagination_custom_param_names_and_start_page() -> None:
    strategy = OffsetPagination(
        page_size=10, page_param="offset", page_size_param="limit", start_page=0
    )
    assert strategy.params_for_page(0, None) == {"offset": 0, "limit": 10}
    assert strategy.params_for_page(1, None) == {"offset": 1, "limit": 10}


def test_offset_pagination_stops_on_a_short_page() -> None:
    strategy = OffsetPagination(page_size=10)
    full_page_response = httpx.Response(200, json=list(range(10)))
    short_page_response = httpx.Response(200, json=list(range(3)))
    assert strategy.has_next_page(full_page_response, list(range(10))) is True
    assert strategy.has_next_page(short_page_response, list(range(3))) is False


def test_cursor_pagination_first_page_has_no_cursor_param() -> None:
    strategy = CursorPagination()
    assert strategy.params_for_page(0, None) == {}


def test_cursor_pagination_extracts_cursor_from_previous_response() -> None:
    strategy = CursorPagination()
    response = httpx.Response(200, json={"next_cursor": "abc123", "items": []})
    assert strategy.params_for_page(1, response) == {"cursor": "abc123"}


def test_cursor_pagination_stops_when_no_cursor_present() -> None:
    strategy = CursorPagination()
    response = httpx.Response(200, json={"items": []})
    assert strategy.has_next_page(response, []) is False


def test_cursor_pagination_stops_gracefully_when_body_is_not_a_dict() -> None:
    """A bare JSON array response - the default guesser has nowhere to
    look for a cursor field, and should treat that as "no more pages"
    rather than crash."""
    strategy = CursorPagination()
    response = httpx.Response(200, json=[1, 2, 3])
    assert strategy.has_next_page(response, [1, 2, 3]) is False


def test_cursor_pagination_custom_field_and_param_name() -> None:
    strategy = CursorPagination(
        cursor_param="continuation",
        next_cursor_from_response=lambda r: r.json().get("continuationToken"),
    )
    response = httpx.Response(200, json={"continuationToken": "xyz"})
    assert strategy.params_for_page(1, response) == {"continuation": "xyz"}
    assert strategy.has_next_page(response, []) is True


# --- APIClient.paginate() -----------------------------------------------


@respx.mock
def test_paginate_walks_offset_pages_and_yields_all_items(api_client: APIClient) -> None:
    respx.get("https://api.test/items", params={"page": 1, "page_size": 2}).mock(
        return_value=httpx.Response(200, json=[{"n": 1}, {"n": 2}])
    )
    respx.get("https://api.test/items", params={"page": 2, "page_size": 2}).mock(
        return_value=httpx.Response(200, json=[{"n": 3}])
    )

    items = list(api_client.paginate("/items", pagination=OffsetPagination(page_size=2)))

    assert items == [{"n": 1}, {"n": 2}, {"n": 3}]


@respx.mock
def test_paginate_makes_one_extra_empty_request_on_an_exact_multiple(
    api_client: APIClient,
) -> None:
    """Documented, accepted tradeoff (see pagination.py's docstring) -
    without an explicit total/has_more field, there's no way to know a
    full page was the last one without fetching the next, empty one."""
    respx.get("https://api.test/items", params={"page": 1, "page_size": 2}).mock(
        return_value=httpx.Response(200, json=[{"n": 1}, {"n": 2}])
    )
    empty_route = respx.get("https://api.test/items", params={"page": 2, "page_size": 2}).mock(
        return_value=httpx.Response(200, json=[])
    )

    items = list(api_client.paginate("/items", pagination=OffsetPagination(page_size=2)))

    assert items == [{"n": 1}, {"n": 2}]
    assert empty_route.called


@respx.mock
def test_paginate_walks_cursor_pages(api_client: APIClient) -> None:
    """side_effect as an ordered list, not two routes distinguished by
    params= - respx treats params={} as "unconstrained", not "must be
    empty", so two separately-matched routes here would both match every
    request and the cursor would never actually advance (this was a real
    bug caught by writing this test, not a hypothetical one - see the
    guide's chapter on it)."""
    respx.get("https://api.test/items").mock(
        side_effect=[
            httpx.Response(200, json={"next_cursor": "page2", "items": [{"n": 1}]}),
            httpx.Response(200, json={"items": [{"n": 2}]}),
        ]
    )

    items = list(
        api_client.paginate(
            "/items",
            pagination=CursorPagination(),
            items_from_response=lambda r: r.json()["items"],
        )
    )

    assert items == [{"n": 1}, {"n": 2}]


@respx.mock
def test_paginate_supports_a_response_envelope(api_client: APIClient) -> None:
    """Matches ServiceNow's Table API shape: {"result": [...]}."""
    respx.get("https://api.test/items", params={"page": 1, "page_size": 50}).mock(
        return_value=httpx.Response(200, json={"result": [{"n": 1}]})
    )

    items = list(
        api_client.paginate(
            "/items",
            pagination=OffsetPagination(),
            items_from_response=lambda r: r.json()["result"],
        )
    )

    assert items == [{"n": 1}]


@respx.mock
def test_paginate_raises_a_helpful_error_if_items_arent_a_list(api_client: APIClient) -> None:
    respx.get("https://api.test/items", params={"page": 1, "page_size": 50}).mock(
        return_value=httpx.Response(200, json={"result": [{"n": 1}]})
    )

    with pytest.raises(TypeError, match="items_from_response"):
        list(api_client.paginate("/items", pagination=OffsetPagination()))


@respx.mock
def test_paginate_respects_max_pages_as_a_safety_cap(api_client: APIClient) -> None:
    """A cursor that never advances would loop forever without this."""
    respx.get("https://api.test/items").mock(
        return_value=httpx.Response(
            200, json={"next_cursor": "same-cursor-forever", "items": [{"n": 1}]}
        )
    )

    items = list(
        api_client.paginate(
            "/items",
            pagination=CursorPagination(),
            items_from_response=lambda r: r.json()["items"],
            max_pages=3,
        )
    )

    assert len(items) == 3


# --- BaseAPIModel.find_iter() --------------------------------------------


@respx.mock
def test_find_iter_without_pagination_degrades_to_a_single_page(api_client: APIClient) -> None:
    route = respx.get("https://api.test/items").mock(
        return_value=httpx.Response(200, json=[{"id": "i1", "name": "A"}])
    )

    items = list(Item.find_iter(client=api_client))

    assert route.call_count == 1
    assert [i.name for i in items] == ["A"]
    assert items[0]._client is api_client


@respx.mock
def test_find_iter_respects_max_pages(api_client: APIClient) -> None:
    respx.get("https://api.test/items").mock(
        return_value=httpx.Response(
            200, json={"next_cursor": "same-cursor-forever", "items": [{"id": "i1", "name": "A"}]}
        )
    )

    items = list(
        Item.find_iter(
            client=api_client,
            pagination=CursorPagination(),
            items_from_response=lambda r: r.json()["items"],
            max_pages=2,
        )
    )

    assert len(items) == 2


@respx.mock
def test_find_iter_with_pagination_yields_bound_instances_across_pages(
    api_client: APIClient,
) -> None:
    respx.get("https://api.test/items", params={"page": 1, "page_size": 1}).mock(
        return_value=httpx.Response(200, json=[{"id": "i1", "name": "A"}])
    )
    respx.get("https://api.test/items", params={"page": 2, "page_size": 1}).mock(
        return_value=httpx.Response(200, json=[{"id": "i2", "name": "B"}])
    )
    respx.get("https://api.test/items", params={"page": 3, "page_size": 1}).mock(
        return_value=httpx.Response(200, json=[])
    )

    items = list(Item.find_iter(client=api_client, pagination=OffsetPagination(page_size=1)))

    assert [i.name for i in items] == ["A", "B"]
    assert all(i._client is api_client for i in items)
