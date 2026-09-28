"""The glue between one APIClient and one resource model type.

A ResourceManager binds one model class (e.g. User) to one APIClient, so
sdk.users.create(...)/.load(...)/.find(...)/.find_iter(...) (see
facade.py) come back already bound and ready for .save()/.delete(), with
no global state anywhere and no client= to pass by hand.
"""

from collections.abc import Callable, Iterator
from typing import Any

import httpx

from .base import BaseAPIModel
from .client import APIClient
from .pagination import PaginationStrategy


class ResourceManager[M: BaseAPIModel]:
    """`[M: BaseAPIModel]` is a PEP 695 generic type parameter: M stands for
    "whatever concrete model this manager was built for". It's what lets
    ResourceManager(User, client).create(...) be typed as returning a User,
    and ResourceManager(Incident, client).create(...) as returning an
    Incident, from one shared, generic implementation.
    """

    def __init__(self, model: type[M], client: APIClient) -> None:
        self._model = model
        self._client = client

    def create(self, **fields: Any) -> M:
        """Build a new instance from `fields` and immediately save (POST) it."""
        instance = self._model(**fields)
        instance.save(client=self._client)
        return instance

    def load(self, resource_id: str) -> M:
        return self._model.load(resource_id, client=self._client)

    def find(self) -> list[M]:
        return self._model.find(client=self._client)

    def find_iter(
        self,
        pagination: PaginationStrategy | None = None,
        items_from_response: Callable[[httpx.Response], list[Any]] = lambda r: r.json(),
        max_pages: int | None = None,
    ) -> Iterator[M]:
        return self._model.find_iter(
            client=self._client,
            pagination=pagination,
            items_from_response=items_from_response,
            max_pages=max_pages,
        )
