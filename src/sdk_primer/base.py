"""BaseAPIModel: save/delete/update/load/find for any pydantic model backed
by a REST resource.

Subclass this, set `_resource_path` to the collection's URL segment (see
examples/user-service/user.py in this repo for a worked example), and get
all five for free.

Where does each method get the APIClient it needs?

- load() and find() are @classmethods - there's no instance yet, so they
  require a `client` to be passed in explicitly.
- save() and delete() run on an existing instance, so instead of asking
  for a client every time, an instance can be *bound* to one once (via
  bind(), or implicitly by going through a ResourceManager - see
  resource.py) and reuse it on every subsequent call.
"""

from collections.abc import Callable, Iterator
from typing import Any, ClassVar, Self

import httpx
from loguru import logger
from pydantic import BaseModel, PrivateAttr

from .client import APIClient
from .exceptions import NotFoundError
from .pagination import PaginationStrategy


class BaseAPIModel(BaseModel):
    id: str | None = None

    # ClassVar: belongs to the class, not instances - each subclass
    # overrides it with its own URL segment (User sets it to "users").
    _resource_path: ClassVar[str] = ""

    # NOT a ClassVar: each individual instance - this particular User, that
    # particular Incident - can be bound to a different client. Leading-
    # underscore attributes on a pydantic model are "private": real
    # per-instance state pydantic won't validate, won't include in
    # model_dump(), and won't accept as a constructor argument.
    _client: APIClient | None = PrivateAttr(default=None)

    def bind(self, client: APIClient) -> None:
        """Attach an APIClient to this instance, so save()/delete() work
        without passing client= each time. Called for you by
        ResourceManager (resource.py) when going through sdk.users.create(...)
        or .load(...); call it yourself if you constructed a model directly.
        """
        self._client = client

    def _resolve_client(self, client: APIClient | None) -> APIClient:
        """Prefer an explicitly-passed client, falling back to whatever
        this instance was bound to, and remembering an explicit one for
        next time."""
        if client is not None:
            self._client = client
        if self._client is None:
            raise RuntimeError(
                f"{type(self).__name__} instance is not bound to an APIClient. "
                f"Create/load it through a ResourceManager (e.g. sdk.users.create(...) "
                f"or sdk.users.load(...)), or call .bind(client) / .save(client=...) yourself."
            )
        return self._client

    def update(self, **fields: Any) -> None:
        """Set several fields at once, locally, without making a request.

        user.update(email="new@example.com")
        user.save()
        """
        for name, value in fields.items():
            setattr(self, name, value)

    def save(self, *, client: APIClient | None = None) -> None:
        """Create the resource if it has no id yet, otherwise update it.

        client.put/post already raise an SDKError subclass (exceptions.py)
        on a non-2xx response, so there's nothing left to check here.
        """
        client = self._resolve_client(client)
        data = self.model_dump(exclude_unset=True, exclude={"id"})
        if self.id:
            logger.info("Updating {} id={}", type(self).__name__, self.id)
            response = client.put(f"/{self._resource_path}/{self.id}", json=data)
        else:
            logger.info("Creating {}", type(self).__name__)
            response = client.post(f"/{self._resource_path}", json=data)
        self.id = response.json()["id"]
        logger.debug("Saved {} id={}", type(self).__name__, self.id)

    def delete(self, *, client: APIClient | None = None) -> None:
        if not self.id:
            logger.error("Attempted to delete an unsaved {}", type(self).__name__)
            raise ValueError("Cannot delete unsaved resource.")
        client = self._resolve_client(client)
        logger.info("Deleting {} id={}", type(self).__name__, self.id)
        client.delete(f"/{self._resource_path}/{self.id}")
        logger.debug("Deleted {} id={}", type(self).__name__, self.id)

    @classmethod
    def load(cls, resource_id: str, *, client: APIClient) -> Self:
        """Fetch one record by id, raising NotFoundError (also a
        ValueError - see exceptions.py) if the server says 404.

        client.get() already translates a 404 into NotFoundError
        (exceptions.py/client.py) - this just catches it to log a
        friendlier, resource-specific message and re-raise, rather than
        checking status codes itself (see the guide's chapter on the bug
        that used to live here, before client.py did this translation).
        """
        logger.debug("Loading {} id={}", cls.__name__, resource_id)
        try:
            response = client.get(f"/{cls._resource_path}/{resource_id}")
        except NotFoundError as exc:
            logger.warning("{} not found id={}", cls.__name__, resource_id)
            raise NotFoundError(f"{cls.__name__} not found.") from exc
        logger.info("Loaded {} id={}", cls.__name__, resource_id)
        instance = cls(**response.json())
        instance.bind(client)
        return instance

    @classmethod
    def find(cls, *, client: APIClient) -> list[Self]:
        """List every record in this resource's collection, in one
        request. Assumes the whole collection fits in one response - for
        a collection large enough to be paginated server-side, use
        find_iter() instead."""
        logger.debug("Listing {}", cls.__name__)
        response = client.get(f"/{cls._resource_path}")
        items = [cls(**item) for item in response.json()]
        logger.info("Found {} {} record(s)", len(items), cls.__name__)
        for item in items:
            item.bind(client)
        return items

    @classmethod
    def find_iter(
        cls,
        *,
        client: APIClient,
        pagination: PaginationStrategy | None = None,
        items_from_response: Callable[[httpx.Response], list[Any]] = lambda r: r.json(),
        max_pages: int | None = None,
    ) -> Iterator[Self]:
        """Yields records one at a time, fetching the next page only as
        needed. Without pagination= (see pagination.py for
        OffsetPagination/CursorPagination), this degrades to the same
        single request find() makes - deliberately, rather than guessing
        a pagination scheme. max_pages caps how many pages will ever be
        fetched - see APIClient.paginate().
        """
        if pagination is None:
            logger.debug("Listing {} (no pagination configured, single page)", cls.__name__)
            yield from cls.find(client=client)
            return
        logger.debug("Listing {} (paginated)", cls.__name__)
        count = 0
        for raw_item in client.paginate(
            f"/{cls._resource_path}",
            pagination=pagination,
            items_from_response=items_from_response,
            max_pages=max_pages,
        ):
            instance = cls(**raw_item)
            instance.bind(client)
            count += 1
            yield instance
        logger.info("Found {} {} record(s) (paginated)", count, cls.__name__)
