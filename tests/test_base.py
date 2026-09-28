"""Tests for BaseAPIModel's save/delete/update/load/find, using a small
resource defined here so these tests exercise the base class on its own,
not through User.

api_client comes from sdk_primer.testing's pytest plugin, not a local
fixture - see conftest.py's absence and the guide's chapter on it.
"""

from typing import ClassVar

import httpx
import pytest
import respx

from sdk_primer import APIClient, ServerError
from sdk_primer.base import BaseAPIModel


class Widget(BaseAPIModel):
    name: str
    _resource_path: ClassVar[str] = "widgets"


def test_save_without_a_bound_or_passed_client_raises(api_client: APIClient) -> None:
    widget = Widget(name="Gadget")
    with pytest.raises(RuntimeError, match="not bound to an APIClient"):
        widget.save()


@respx.mock
def test_save_creates_when_unsaved(api_client: APIClient) -> None:
    route = respx.post("https://api.test/widgets").mock(
        return_value=httpx.Response(201, json={"id": "w1"})
    )
    widget = Widget(name="Gadget")
    widget.save(client=api_client)
    assert route.called
    assert widget.id == "w1"


@respx.mock
def test_save_remembers_the_client_for_next_time(api_client: APIClient) -> None:
    """save(client=...) once, then plain save() again, should reuse it."""
    respx.post("https://api.test/widgets").mock(return_value=httpx.Response(201, json={"id": "w1"}))
    update_route = respx.put("https://api.test/widgets/w1").mock(
        return_value=httpx.Response(200, json={"id": "w1"})
    )
    widget = Widget(name="Gadget")
    widget.save(client=api_client)
    widget.update(name="Gadget 2")
    widget.save()
    assert update_route.called


@respx.mock
def test_save_updates_when_already_has_id(api_client: APIClient) -> None:
    route = respx.put("https://api.test/widgets/w1").mock(
        return_value=httpx.Response(200, json={"id": "w1"})
    )
    widget = Widget(id="w1", name="Gadget")
    widget.save(client=api_client)
    assert route.called


def test_delete_unsaved_raises(api_client: APIClient) -> None:
    widget = Widget(name="Gadget")
    with pytest.raises(ValueError, match="Cannot delete unsaved"):
        widget.delete(client=api_client)


@respx.mock
def test_delete_saved_calls_endpoint(api_client: APIClient) -> None:
    route = respx.delete("https://api.test/widgets/w1").mock(return_value=httpx.Response(204))
    widget = Widget(id="w1", name="Gadget")
    widget.delete(client=api_client)
    assert route.called


@respx.mock
def test_load_returns_bound_instance(api_client: APIClient) -> None:
    respx.get("https://api.test/widgets/w1").mock(
        return_value=httpx.Response(200, json={"id": "w1", "name": "Gadget"})
    )
    widget = Widget.load("w1", client=api_client)
    assert isinstance(widget, Widget)
    assert widget.name == "Gadget"
    assert widget._client is api_client  # bound automatically, save() needs no client=


@respx.mock
def test_load_missing_raises_value_error(api_client: APIClient) -> None:
    respx.get("https://api.test/widgets/missing").mock(return_value=httpx.Response(404))
    with pytest.raises(ValueError, match="not found"):
        Widget.load("missing", client=api_client)


@respx.mock
def test_load_other_error_status_propagates_as_server_error(api_client: APIClient) -> None:
    """A 500 (or any non-404 error) should NOT be swallowed into
    NotFoundError - only 404 gets the "not found" treatment."""
    respx.get("https://api.test/widgets/w1").mock(return_value=httpx.Response(500))
    with pytest.raises(ServerError):
        Widget.load("w1", client=api_client)


@respx.mock
def test_find_returns_bound_instances(api_client: APIClient) -> None:
    respx.get("https://api.test/widgets").mock(
        return_value=httpx.Response(
            200, json=[{"id": "w1", "name": "A"}, {"id": "w2", "name": "B"}]
        )
    )
    widgets = Widget.find(client=api_client)
    assert [w.name for w in widgets] == ["A", "B"]
    assert all(w._client is api_client for w in widgets)


def test_update_sets_fields_locally_without_a_request() -> None:
    widget = Widget(name="Gadget")
    widget.update(name="Gadget 2")
    assert widget.name == "Gadget 2"
