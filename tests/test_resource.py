"""Tests for ResourceManager on its own, using a second resource (not User)
to prove the generic binding isn't special-cased to any one model.

api_client comes from sdk_primer.testing's pytest plugin.
"""

from typing import ClassVar

import httpx
import respx

from sdk_primer import APIClient, OffsetPagination
from sdk_primer.base import BaseAPIModel
from sdk_primer.resource import ResourceManager


class Product(BaseAPIModel):
    title: str
    price: float
    _resource_path: ClassVar[str] = "products"


@respx.mock
def test_create_saves_and_returns_bound_instance(api_client: APIClient) -> None:
    respx.post("https://api.test/products").mock(
        return_value=httpx.Response(201, json={"id": "p1"})
    )
    products = ResourceManager(Product, api_client)

    product = products.create(title="Widget", price=9.99)

    assert product.id == "p1"
    assert product._client is api_client


@respx.mock
def test_created_instance_updates_and_saves_without_extra_wiring(api_client: APIClient) -> None:
    respx.post("https://api.test/products").mock(
        return_value=httpx.Response(201, json={"id": "p1"})
    )
    update_route = respx.put("https://api.test/products/p1").mock(
        return_value=httpx.Response(200, json={"id": "p1"})
    )
    products = ResourceManager(Product, api_client)

    product = products.create(title="Widget", price=9.99)
    product.update(price=12.99)
    product.save()

    assert update_route.called
    assert product.price == 12.99


@respx.mock
def test_load_and_find_delegate_to_the_bound_client(api_client: APIClient) -> None:
    respx.get("https://api.test/products/p1").mock(
        return_value=httpx.Response(200, json={"id": "p1", "title": "Widget", "price": 9.99})
    )
    respx.get("https://api.test/products").mock(
        return_value=httpx.Response(200, json=[{"id": "p1", "title": "Widget", "price": 9.99}])
    )
    products = ResourceManager(Product, api_client)

    loaded = products.load("p1")
    found = products.find()

    assert loaded.id == "p1"
    assert [p.id for p in found] == ["p1"]


@respx.mock
def test_find_iter_delegates_to_the_bound_client(api_client: APIClient) -> None:
    """Same coverage as test_load_and_find_delegate_to_the_bound_client,
    for find_iter() - proves ResourceManager.find_iter() (resource.py)
    passes pagination= through to BaseAPIModel.find_iter() unchanged."""
    respx.get("https://api.test/products", params={"page": 1, "page_size": 1}).mock(
        return_value=httpx.Response(200, json=[{"id": "p1", "title": "Widget", "price": 9.99}])
    )
    respx.get("https://api.test/products", params={"page": 2, "page_size": 1}).mock(
        return_value=httpx.Response(200, json=[{"id": "p2", "title": "Gadget", "price": 19.99}])
    )
    respx.get("https://api.test/products", params={"page": 3, "page_size": 1}).mock(
        return_value=httpx.Response(200, json=[])
    )
    products = ResourceManager(Product, api_client)

    found = list(products.find_iter(pagination=OffsetPagination(page_size=1)))

    assert [p.id for p in found] == ["p1", "p2"]
    assert all(p._client is api_client for p in found)


@respx.mock
def test_two_resource_managers_against_different_backends_dont_interfere() -> None:
    """The motivating case from the instantiable-client redesign (chapter
    14): two connections (could be two servers of the same service, or two
    different services entirely) alive at once, each independently bound.
    """
    route_a = respx.post("https://a.test/products").mock(
        return_value=httpx.Response(201, json={"id": "a1"})
    )
    route_b = respx.post("https://b.test/products").mock(
        return_value=httpx.Response(201, json={"id": "b1"})
    )

    client_a = APIClient(base_url="https://a.test", token="token-a", sleep=lambda _: None)
    client_b = APIClient(base_url="https://b.test", token="token-b", sleep=lambda _: None)
    products_a = ResourceManager(Product, client_a)
    products_b = ResourceManager(Product, client_b)

    product_a = products_a.create(title="Widget A", price=9.99)
    product_b = products_b.create(title="Widget B", price=19.99)

    assert route_a.called
    assert route_b.called
    assert product_a.id == "a1"
    assert product_b.id == "b1"
    # each stayed bound to its own client, not the other one's
    assert product_a._client is client_a
    assert product_b._client is client_b
