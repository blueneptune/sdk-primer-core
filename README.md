# sdk-primer

Built starting from the [ArjanCodes sdk_v3 example](https://github.com/ArjanCodes/examples/tree/main/2025/sdk/sdk_v3)
(`2025/sdk/sdk_v3` in that repo) - a small facade over a REST API: a thin HTTP client, a
`BaseAPIModel` with save/load/find, one example resource. `sdk-primer` is where that pattern got
hardened into something meant to actually be depended on: logging on every call, an instantiable
`APIClient` (so any number of independently-configured connections can coexist in one process),
retry/backoff with idempotency keys, fixed correctness bugs the original example has, full test
coverage, and a `[build-system]` so it's a real, independently installable/extractable project
rather than a folder of loose scripts. The original example itself was kept in this repo for a
while as a frozen, unmodified reference (see the guide's chapter on removing it) - by this point
everything worth knowing about it is captured in `sdk-primer`'s own code, tests and the guide
below, so the copy was retired.

Meant as a base to depend on when building others like it. This package ships **only the reusable
engine** - `BaseAPIModel`, `APIClient`, `ResourceManager`, `configure_logging`, and the `testing`
fixtures. No resource, no facade, and no worked example ship inside it: a real project depending on
`sdk-primer` is a NetBackup SDK, a Rubrik SDK, not a library of `User` objects. `examples/user-service`
in [`blueneptune/yoki-sdk-primer`](https://github.com/blueneptune/yoki-sdk-primer) - the combined
repo this package was split out of - is what a real project built on top of this actually looks
like: its own `user.py`, its own `facade.py` - the shape to copy, written as first-party code in the
consuming project, not something this package provides.

The full narrative of *why* it's built this way - not just what the code does - is the chaptered
build-journal guide at
[`yoki-sdk-primer/guide`](https://github.com/blueneptune/yoki-sdk-primer/tree/main/guide), including
the chapter on why this package moved into its own repo.

## Quickstart

```python
from sdk_primer import APIClient, BaseAPIModel

class User(BaseAPIModel):
    name: str
    email: str
    _resource_path = "users"

client = APIClient(base_url="https://api.example.com", token="your-token")
user = User(name="Ada", email="ada@example.com")
user.save(client=client)

same_user = User.load(user.id, client=client)
everyone = User.find(client=client)
```

For the nicer `sdk.users.create(...)` facade ergonomics, write a small `ResourceManager`-based
wrapper the way `examples/user-service/facade.py` does - a few lines, specific to your project, not
something this package provides for you:

```python
from sdk_primer import APIClient, ResourceManager

class SDK:
    def __init__(self, client: APIClient) -> None:
        self.users = ResourceManager(User, client)

sdk = SDK(client)
user = sdk.users.create(name="Ada", email="ada@example.com")
user.update(email="ada.lovelace@example.com")
user.save()
```

Each `APIClient` is independent - construct as many as you need (two connections to the same
service, or connections to different services entirely) and they won't interfere with each other.

## Retries and idempotency

A 503, a 502, a 504, a 429, or a network error retries automatically (3 attempts by default,
exponential backoff with jitter, or a 429's own `Retry-After` header when the server sends one).
A plain 4xx doesn't retry - that's a real bug in the request, not a transient condition - and
neither does a plain 500 - usually a real bug on the server, where retrying is unlikely to help.

Every `POST` carries an idempotency key (`Idempotency-Key` header by default), generated once per
logical call and reused across all of that call's retries, so a retried create can't produce a
duplicate resource server-side even if the original request actually landed and only its response
was lost:

```python
client = APIClient(
    base_url="https://api.example.com",
    token="your-token",
    max_attempts=5,               # default 3
    backoff_base=1.0,             # default 0.5 (seconds)
    idempotency_header="X-Idempotency-Key",  # default "Idempotency-Key"
)

# or supply your own key instead of an auto-generated one:
client.post("/users", json={"name": "Ada"}, idempotency_key="my-own-key")
```

## Exceptions

Every failed request raises a typed exception, not a raw `httpx` one - `httpx.HTTPStatusError`/
`HTTPError` are still reachable via `__cause__`, but a caller doesn't need to know them to handle
errors meaningfully:

```python
from sdk_primer import NotFoundError, RateLimitedError, SDKError, ValidationError

try:
    user = User.load(user_id, client=client)
except NotFoundError:
    ...
except RateLimitedError as exc:
    print(f"rate limited, retry after {exc.retry_after}s")
except ValidationError as exc:
    print(f"server rejected the request: {exc.detail}")
except SDKError:
    ...  # anything else
```

| Status | Exception |
|---|---|
| 400, 422 | `ValidationError` (`.detail` - the server's own error payload) |
| 401 | `AuthenticationError` |
| 404 | `NotFoundError` (also a `ValueError`, for backward compatibility) |
| 429 | `RateLimitedError` (`.retry_after`, seconds, if the server sent one) |
| other 4xx | `ClientError` |
| 5xx | `ServerError` |
| connection never completed | `NetworkError` |

All of them are `SDKError` subclasses - `except SDKError` catches any of them generically.

## Authentication

`APIClient(token=...)` is a shorthand for the simple static-Bearer-token case. For anything else,
`APIClient(auth=...)` accepts any [`httpx.Auth`](https://www.python-httpx.org/advanced/authentication/)
instance - built on a standard mechanism rather than one invented here, so it composes with
retries (a request retried after backoff re-runs the auth flow too) and can react to a 401.

`TokenExchangeAuth` implements one common, reusable shape directly: POST some credentials to a
token endpoint once, cache the token it gets back, and transparently re-exchange it on a 401 or
once a TTL elapses:

```python
from sdk_primer import APIClient, TokenExchangeAuth

# client_id/client_secret POSTed to a token endpoint (OAuth2 client-credentials shape):
auth = TokenExchangeAuth(
    token_url="https://api.example.com/oauth/token",
    credentials={"client_id": "...", "client_secret": "..."},
)

# a login endpoint returning the token under a field name other than the
# defaults (access_token/token/session_token) this class guesses first:
auth = TokenExchangeAuth(
    token_url="https://api.example.com/login",
    credentials={"userName": "...", "password": "..."},
    token_from_response=lambda r: r.json()["sessionToken"],
    ttl_seconds=3600,  # proactively refresh before it expires, not just on 401
)

client = APIClient(base_url="https://api.example.com", auth=auth)
```

For a static API key sent as a fixed header (no exchange, no per-request signing), skip `auth=`
entirely and use `default_headers` instead:

```python
client = APIClient(base_url="https://api.example.com", default_headers={"X-Api-Key": "..."})
```

## Pagination

`find()` fetches one response and assumes the whole collection fits in it. For a collection large
enough to be paginated server-side, `find_iter()` walks every page lazily instead, yielding records
one at a time as it fetches them:

```python
from sdk_primer import OffsetPagination

for user in User.find_iter(client=client, pagination=OffsetPagination(page_size=50)):
    print(user.name)
```

`OffsetPagination` covers `?page=N&page_size=M`-style APIs (param names and starting page are all
configurable). `CursorPagination` covers a cursor/token in the response body becoming a query param
on the next request - it guesses the field name (`next_cursor`/`next`/`cursor`) by default, or takes
`next_cursor_from_response=` for anything else:

```python
from sdk_primer import CursorPagination

pagination = CursorPagination(
    cursor_param="continuation",
    next_cursor_from_response=lambda r: r.json().get("continuationToken"),
)
for user in User.find_iter(client=client, pagination=pagination):
    print(user.name)
```

Without `pagination=`, `find_iter()` deliberately falls back to the same single request `find()`
already makes, rather than guessing a scheme - a wrong guess risks an infinite loop or silently
missing data, worse than just not paginating.

Some APIs wrap results in an envelope instead of returning a bare array - ServiceNow's Table API
returns `{"result": [...]}`, for example. Pass `items_from_response=` to unwrap it:

```python
for user in User.find_iter(
    client=client,
    pagination=OffsetPagination(),
    items_from_response=lambda r: r.json()["result"],
):
    print(user.name)
```

`max_pages=` caps how many pages will ever be fetched, as a safety valve against a misconfigured or
misbehaving server whose cursor never advances - nothing else here can tell "genuinely more data"
apart from "a cursor stuck in place":

```python
User.find_iter(client=client, pagination=CursorPagination(), max_pages=1000)
```

`ResourceManager.find_iter(...)` (the facade layer - see Quickstart above) takes the same
`pagination=`/`items_from_response=`/`max_pages=` arguments and passes them through unchanged, e.g.
`sdk.users.find_iter(pagination=OffsetPagination(page_size=50))`.

## Logging

sdk_primer logs through [loguru](https://github.com/Delgan/loguru)'s global `logger`, which writes
INFO-and-above to stderr by default - nothing to configure to see it. To change the level or
destination:

```python
from sdk_primer import configure_logging

configure_logging(level="DEBUG")
```

Every `APIClient.request()` call binds a fresh `request_id` (a `logger.bind(request_id=...)`
context) once per logical call, reused across every retry attempt of that same call - so a format
string referencing `{extra[request_id]}`, or a `serialize=True` sink (which includes every extra
field automatically), lets one request's full retry history be grepped or filtered as a unit even
when several attempts interleave with other calls' log lines. A log line from outside `request()`
has `request_id=None` rather than raising a `KeyError` against that format string.

## Testing

sdk_primer registers a pytest plugin (`sdk_primer.testing`) - any project with sdk_primer installed
(via the `[testing]` extra) gets these fixtures for free, no conftest.py wiring needed:

- `api_client` - a throwaway `APIClient` pointed at `https://api.test` (IANA's reserved test TLD),
  ready for `respx` to intercept.
- `log_messages` - captures every loguru message emitted during a test, for asserting on logging
  behavior (e.g. that a token got redacted).

`sdk-primer`'s own tests use both - see `tests/`.

## Development

```
uv sync
uv run pytest                          # unit tests, mocked HTTP via respx
uv run coverage run -m pytest && uv run coverage report
uv run ruff check .
uv run ruff format .
uv run mypy
```

`uv run coverage run -m pytest`, not `pytest --cov=...`: because sdk_primer is itself a pytest
plugin, pytest imports it during its own early plugin-loading phase, before `pytest-cov`'s `--cov`
flag would start tracing - `coverage run` starts tracing before `pytest` itself is even imported,
so it doesn't miss that early import. See `[tool.coverage.run]` in this file.

## Adapting this for a new service

See `examples/user-service` in
[`blueneptune/yoki-sdk-primer`](https://github.com/blueneptune/yoki-sdk-primer), and that repo's
guide's "Adapting the template" and "A real example project" chapters, for worked examples. In short: write
a `BaseAPIModel` subclass with your resource's fields and `_resource_path` for each resource type
your target service exposes, register it on your own small `ResourceManager`-based facade the way
`examples/user-service/user.py` / `facade.py` show, and construct an `APIClient` per connection.

To build a whole new SDK project on top of this one, depend on it rather than copying it - a git
dependency pinned to a tag:

```
uv add git+https://github.com/blueneptune/sdk-primer-core.git@v0.1.1
```

so fixes and improvements here reach every project built on it by bumping a version, rather than
needing to be manually re-applied to each copy. This package is deliberately kept bare (no
example/facade code, nothing beyond the engine) so that dependency stays genuinely lightweight -
`examples/user-service` in `yoki-sdk-primer` depends on this repo the exact same way, proving it
works before any real NetBackup/Rubrik/ServiceNow SDK does the same.

## Roadmap

See [`TODO.md`](TODO.md) for the known gaps - retry/backoff, idempotency, a typed exception
hierarchy, pluggable authentication, pagination, and correlation IDs in logs are done; a few smaller
ones remain, roughly in the order they're worth building.
