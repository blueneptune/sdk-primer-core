"""sdk_primer: the reusable engine for a facade-style SDK wrapping a REST API.

    from sdk_primer import APIClient, BaseAPIModel

    class User(BaseAPIModel):
        name: str
        email: str
        _resource_path = "users"

    client = APIClient(base_url="https://api.example.com", token="your-token")
    user = User(name="Ada", email="ada@example.com")
    user.save(client=client)

That's genuinely everything this package ships: BaseAPIModel (subclass it
per resource), APIClient (one per connection), ResourceManager (binds one
model class to one client, for a facade like the example project builds -
see below), configure_logging(), a typed exception hierarchy
(exceptions.py) so callers can catch NotFoundError/AuthenticationError/
RateLimitedError/ValidationError/ServerError/ClientError/NetworkError -
all SDKError subclasses - instead of raw httpx exceptions, and pluggable
authentication (auth.py): APIClient(token=...) for the simple static-
token case, APIClient(auth=SomeHttpxAuth()) for anything else -
TokenExchangeAuth covers the common "POST credentials once, get a token,
use it, refresh it" pattern real services like NetBackup and Rubrik
need. And pluggable pagination (pagination.py): find() makes one request
and assumes the whole collection fits; find_iter() walks pages as needed
given an OffsetPagination or CursorPagination (or a custom
PaginationStrategy). No resource, no facade, and no worked example ship
inside sdk_primer itself - a real project depending on this is a
NetBackup SDK, a Rubrik SDK, not a library of User objects.

See examples/user-service in this repo for what a real project built on
top of this actually looks like: its own user.py (a BaseAPIModel
subclass), its own facade.py (a ResourceManager-based SDK class wired to
that resource) - the shape to copy, written as first-party code in the
consuming project rather than borrowed from here.

Each APIClient is independent - construct as many as you need (two
connections to the same service, or connections to different services
entirely) and they won't interfere with each other. See
tests/test_concurrency.py for sharing one APIClient safely across threads.
"""

from ._logging import configure_logging
from .auth import TokenExchangeAuth
from .base import BaseAPIModel
from .client import APIClient
from .exceptions import (
    AuthenticationError,
    ClientError,
    NetworkError,
    NotFoundError,
    RateLimitedError,
    SDKError,
    ServerError,
    ValidationError,
)
from .pagination import CursorPagination, OffsetPagination, PaginationStrategy
from .resource import ResourceManager

__all__ = [
    "APIClient",
    "AuthenticationError",
    "BaseAPIModel",
    "ClientError",
    "CursorPagination",
    "NetworkError",
    "NotFoundError",
    "OffsetPagination",
    "PaginationStrategy",
    "RateLimitedError",
    "ResourceManager",
    "SDKError",
    "ServerError",
    "TokenExchangeAuth",
    "ValidationError",
    "configure_logging",
]
