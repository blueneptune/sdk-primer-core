"""Pluggable pagination for BaseAPIModel.find_iter() / APIClient.paginate().

Real REST APIs don't agree on how to paginate a collection - offset/page
query params, a cursor/token in the response body, a Link header, all
show up in practice. Rather than guess one shape, find_iter() takes a
PaginationStrategy (a small two-method protocol) that knows how to build
each page's request and how to tell when there isn't a next one.
OffsetPagination and CursorPagination below cover the two most common
shapes; a custom one is just a class implementing the same two methods.

find_iter() without pagination= falls back to a single page (the same
request find() already makes) rather than guessing a scheme - a wrong
guess here risks an infinite loop or silently missing data, worse than
just not paginating.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

import httpx


class PaginationStrategy(Protocol):
    """Implement these two methods to support a new pagination shape.
    Both are called once per page - params_for_page() to build the
    request, has_next_page() to decide whether to fetch another."""

    def params_for_page(
        self, page_number: int, previous_response: httpx.Response | None
    ) -> dict[str, Any]:
        """Query params for this page. page_number starts at 0;
        previous_response is None only for the first page."""
        ...

    def has_next_page(self, response: httpx.Response, items: list[Any]) -> bool:
        """Whether to fetch another page after this one."""
        ...


class OffsetPagination:
    """?page=N&page_size=M (configurable param names), stopping once a
    page comes back with fewer than page_size items.

    Without an explicit total count or has_more flag from the server,
    there's no way to know for certain a page was the last one without
    fetching it - so when a collection's size happens to be an exact
    multiple of page_size, this makes one extra, empty request at the
    end. A harmless, known tradeoff for a default that has to work
    without knowing the target API's exact response shape; a
    service-specific strategy using an explicit has_more/total field can
    avoid it.
    """

    def __init__(
        self,
        page_size: int = 50,
        page_param: str = "page",
        page_size_param: str = "page_size",
        start_page: int = 1,
    ) -> None:
        self.page_size = page_size
        self.page_param = page_param
        self.page_size_param = page_size_param
        self.start_page = start_page

    def params_for_page(
        self, page_number: int, previous_response: httpx.Response | None
    ) -> dict[str, Any]:
        return {
            self.page_param: self.start_page + page_number,
            self.page_size_param: self.page_size,
        }

    def has_next_page(self, response: httpx.Response, items: list[Any]) -> bool:
        return len(items) >= self.page_size


def _default_next_cursor_from_response(response: httpx.Response) -> str | None:
    """Looks for a next-page cursor under the field names REST APIs most
    commonly use. Unlike auth.py's token-guessing default, this doesn't
    raise when none match - for most APIs, the last page's response
    simply omits the field, which correctly means "no more pages," not
    "misconfigured." Pass your own next_cursor_from_response= if your
    service uses a different field name."""
    body = response.json()
    if not isinstance(body, dict):
        return None
    for key in ("next_cursor", "next", "cursor"):
        value = body.get(key)
        if value:
            return str(value)
    return None


class CursorPagination:
    """A cursor/token from the response body becomes a query param on the
    next request; stops once next_cursor_from_response() returns None.
    Stateless - one instance can be reused across multiple find_iter()
    calls, since the cursor for "the next page" is always re-derived from
    whatever response is passed in, never cached on self.
    """

    def __init__(
        self,
        cursor_param: str = "cursor",
        next_cursor_from_response: Callable[
            [httpx.Response], str | None
        ] = _default_next_cursor_from_response,
    ) -> None:
        self.cursor_param = cursor_param
        self.next_cursor_from_response = next_cursor_from_response

    def params_for_page(
        self, page_number: int, previous_response: httpx.Response | None
    ) -> dict[str, Any]:
        if previous_response is None:
            return {}
        return {self.cursor_param: self.next_cursor_from_response(previous_response)}

    def has_next_page(self, response: httpx.Response, items: list[Any]) -> bool:
        return self.next_cursor_from_response(response) is not None
