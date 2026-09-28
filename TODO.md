# TODO

Known gaps in `sdk_primer`, roughly in the order I'd build them. None of these are bugs in the
sense chapter 11's two fixes were - the SDK works correctly today, within a narrower scope than a
production SDK against NetBackup/Rubrik/ServiceNow would eventually need. Numbers are kept stable
as items get done (struck through in place) rather than renumbered, so a past reference to "item 8"
still means the same thing.

## ~~1. Retry/backoff on transient failures~~

**Done**, idempotency keys included (built together, as noted below - not split apart). Lives in
`APIClient.request()`: retries `httpx.HTTPError` (network failures) and status codes
`{429, 502, 503, 504}` - never a plain 4xx, and deliberately *not* 500 (usually a real bug on the
server, not a transient condition; retrying is unlikely to help and can mask it). Respects a 429's
own `Retry-After` header when it's a plain-seconds value; otherwise exponential backoff with full
jitter. `POST` gets an auto-generated (or caller-supplied) idempotency key, reused across every
retry of that same logical call, via a configurable header (`Idempotency-Key` by default).
`max_attempts`, `backoff_base` and `idempotency_header` are all constructor options on
`APIClient`, with `sleep` injectable the same way `transport` already was, so tests don't wait
through real delays. See `tests/test_retry.py` and the guide's chapter on it.

## ~~2. A typed exception hierarchy~~

**Done**, together with item 9 (they're one module). `exceptions.py`: `SDKError` at the root, with
`ValidationError` (400/422, carries `detail`), `AuthenticationError` (401), `NotFoundError` (404,
also a `ValueError` - see below), `RateLimitedError` (429, carries `retry_after`), `ClientError`
(any other 4xx), `ServerError` (5xx), and `NetworkError` (connection never completed at all - not
in the item's original sketch, added for the same reason as the rest). `client.py`'s
`_exception_for()` translates a failed response into the right one, once retries (item 1) are
exhausted; the original `httpx` exception is preserved as `__cause__`.

`base.load()`'s 404 -> `ValueError` special case (chapter 11) is gone as a special case: `client.get()`
now raises `NotFoundError` directly, and `NotFoundError(SDKError, ValueError)` - multiple
inheritance - means every existing `except ValueError` call site (including this project's own
tests and `examples/user-service`) kept working unchanged, while gaining `except NotFoundError` /
`except SDKError` as more specific options. `load()` itself got simpler: no more manual
`response.status_code == 404` check, no more `import httpx` in `base.py` at all.

## ~~3. Pagination ("find_iter")~~

**Done.** `pagination.py`: a `PaginationStrategy` protocol (`params_for_page()` / `has_next_page()`),
with `OffsetPagination` (`?page=N&page_size=M`, configurable param names) and `CursorPagination` (a
cursor/token from the response body becomes a query param on the next request, guessing common field
names by default) built in. `APIClient.paginate()` walks pages via a strategy, yielding raw items;
`BaseAPIModel.find_iter()` / `ResourceManager.find_iter()` wrap that into an iterator of bound
model instances. Without `pagination=`, `find_iter()` deliberately falls back to the same single
request `find()` already makes, rather than guessing a scheme. `items_from_response=` handles
response envelopes (e.g. ServiceNow's `{"result": [...]}`). `max_pages=` caps how many pages will
ever be fetched - a safety valve against a misconfigured or misbehaving server whose cursor never
advances, directly motivated by "what stops this from scrolling infinitely" - threaded through
`find_iter()`/`ResourceManager.find_iter()`, not just `paginate()` itself.

Two real bugs were caught by writing the tests, not hypothetical ones: a respx test distinguishing
two mocked pages by `params=` never actually advanced, because respx treats `params={}` as
"unconstrained" rather than "must be empty" - both requests matched the first route, the mocked
cursor never changed, and `paginate()`'s `while True` looped for real (1.7GB and climbing before it
was killed) until the test was rewritten to use an ordered `side_effect=` list instead. Separately,
the default `items_from_response` was originally `lambda r: list(r.json())` - `list()` on a dict
returns its *keys*, so an unconfigured envelope response silently became a list containing one
string instead of raising the intended `TypeError`. Both are covered by regression tests now. See
the guide's chapter on it.

Proven against a real live server, not just respx: `examples/user-service/backend/app.py`'s `/users`
route takes real `page`/`page_size` query params, and `tests/test_integration.py` drives
`find_iter(pagination=OffsetPagination(...))` against it, seeding enough users to force more than
one genuine HTTP round trip. 89 tests total in `sdk-primer`, 100% coverage including `pagination.py`.

## ~~4. Pluggable authentication (was: token refresh)~~

**Done.** `auth.py`:

1. **`APIClient(auth: httpx.Auth | None = None, token: str | None = None, default_headers:
   dict[str, str] | None = None, ...)`** - built on httpx's own `Auth` interface, so it composes
   with chapter 17's retry loop for free and can react to a 401. `token=` still works exactly as
   before for the simple case; passing both `token=` and `auth=` raises `ValueError`.
2. **`TokenExchangeAuth`** - the reusable "POST credentials once, get a token, use it, refresh it"
   implementation. `token_from_response` (defaults to guessing `access_token`/`token`/
   `session_token`, override for anything else), `auth_scheme`/`auth_header` (defaults to `Bearer`
   in `Authorization`, either can be changed or `auth_scheme=None` for a raw unprefixed token),
   `ttl_seconds` (proactive refresh) and automatic re-exchange-and-retry-once on a 401 are all
   configuration, not code - the same class covers Rubrik's `client_id`/`client_secret` exchange
   and NetBackup's login flow.
3. **`default_headers`** covers NetBackup's other mechanism (a static API key in a header) without
   needing an `Auth` subclass at all.

Proven against a real live server, not just respx: `examples/user-service/backend/app.py` grew a
`/login` endpoint, and `tests/test_integration.py` drives `TokenExchangeAuth` against it for real
before this ever points at NetBackup/Rubrik. 11 new tests in `test_auth.py`, 71 tests total, 100%
coverage including `auth.py`. See the guide's chapter on it.

## 5. Timeouts and connection pool limits

~~Timeouts~~ **Done** - `APIClient(..., timeout=10.0)`.

Connection pool limits are still open: `APIClient` doesn't expose `httpx.Limits` yet, so it uses
httpx's defaults (100 max connections / 20 keep-alive). A thread pool larger than that (see
`tests/test_concurrency.py`) will queue rather than run fully concurrently.

## 6. The `exclude_unset` partial-update gap

~~An `.update(**fields)` method~~ **Done** - sets several fields locally without a request.

The gap it doesn't fix is still open: once a resource is loaded from the server
(`cls(**response.json())`), *every* field is already "set" on the pydantic model, so `save()`'s
`self.model_dump(exclude_unset=True)` always sends the whole object on update - `.update()` is a
nicer way to *change* fields, but doesn't narrow what gets sent, since the fields it touches were
already "set" from the initial load. Actually fixing this means tracking which fields changed
*since load*, not since construction - a bigger change than `.update()` alone.

## ~~7. Closing the client / context manager support~~

**Done.** `APIClient.close()`, plus `with APIClient(...) as client:`.

## ~~8. An instantiable client, not just module globals~~

**Done.** `APIClient` is a real class you construct - `client.py`, `base.py`, `resource.py`. Any
number of independently-configured clients coexist in one process (`tests/test_resource.py`'s
two-independent-connections test, `examples/user-service/tests/test_integration.py`'s two-live-
servers test). This was the motivating item for everything else on this list - see the guide's
chapter on it.

## ~~9. Structured validation errors~~

**Done**, built together with item 2 - see there. `ValidationError.detail` carries the server's
parsed JSON body (or raw text, if the response wasn't JSON) for a 400 or 422, not just a generic
"request failed" message.

## 10. A `User-Agent` header

`sdk-primer/0.1.0 (python-httpx)`, or similar, sent on every request. Trivial to add in
`APIClient.__init__`'s `httpx.Client(headers={...})` call, and several real API providers
(ServiceNow among them) ask for one so they can identify SDK traffic in their own logs.

## ~~11. Correlation IDs in logs~~

**Done.** `APIClient.request()` binds a fresh `request_id` (`logger.bind(request_id=uuid4().hex)`)
once per logical call, reused across every retry attempt of that same call - the exact case the item
called out, since item 1's retries can turn one call into several log lines. `_logging.py` sets
`logger.configure(extra={"request_id": None})` at import time so a format string referencing
`{extra[request_id]}` (or a `serialize=True` structured-logging sink) never `KeyError`s on a log line
from outside `request()` that has nothing bound, rather than every log line accidentally picking one
up. Scoped deliberately to `request()` itself, not every log line in the codebase - `base.py`'s
"Creating User" before a `save()` call isn't part of the retry sequence the item was about.

## 12. Schema drift detection

pydantic silently ignores fields it doesn't recognize by default. For an SDK that has to stay in
sync with several external APIs that can change underneath it, logging a warning when the server
sends a field the model doesn't know about would catch drift early instead of silently dropping
data - worth an explicit `model_config` decision rather than leaving the default unexamined.

## 13. `Settings.from_env()`

Read `token`/`base_url` from environment variables as an alternative to always passing them to
`APIClient(...)` in code - standard for anything meant to run unchanged across dev/staging/prod.

## 14. Bulk operations

`save_many()` / a batch-create helper, for services that support it (ServiceNow's Import Set API
is one). Directly useful for the kind of sync workloads a backup/ITSM SDK actually does, where
one-record-at-a-time round trips add up fast.

## ~~15. A reusable `sdk_primer.testing` module~~

**Done.** `sdk_primer/testing/` (a subpackage, not a flat module - see the guide's chapter on
separating it from the engine and the example), registered as a real pytest plugin
(`[project.entry-points.pytest11]` in `sdk-primer/pyproject.toml`) behind an optional `[testing]`
extra - `api_client` and `log_messages` fixtures, no conftest.py wiring needed in a downstream
project. `sdk-primer`'s own tests were converted to use it, which is what proved it actually works
as a plugin and not just a local import. One real side effect discovered building this: sdk_primer
becoming a pytest plugin means pytest imports it during its own early startup, which coverage.py's
`pytest --cov=` flag missed entirely (0% on modules that clearly ran) - fixed by switching to
`coverage run -m pytest`, which starts tracing before `pytest` itself is imported. See the guide's
chapters on both.

## Stretch goal: OpenAPI-driven resource generation

Qualitatively different from everything above - a tool, not a feature. If a target service
publishes an OpenAPI spec, generating `BaseAPIModel` subclasses from it instead of hand-writing
each one (see the guide's chapters 12-13) would cut the adaptation work dramatically. Worth
keeping in view, not worth starting before the items above are solid.

## ~~Split into its own git repo~~

**Done.** This is that repo - `blueneptune/sdk-primer-core`, split out of the combined
`sdk-primer-v2` repo (guide + `examples/user-service`) once real downstream consumption started:
multiple separate SDK projects pulling this in as a dependency, where `uv add
git+.../sdk-primer-v2.git#subdirectory=sdk-primer` would have dragged the whole guide and example
project along with every clone, not just the engine. Referenced back from `sdk-primer-v2` as a git
submodule at the same `sdk-primer/` path, so that repo still gets everything (guide, this engine's
source for local dev, `examples/user-service`) in one clone, while `examples/user-service` itself
now depends on this repo directly via `uv add git+https://github.com/blueneptune/sdk-primer-core.git@v0.1.0`
- no `#subdirectory=` needed any more, proving the split actually works rather than just declaring
it. See `sdk-primer-v2/guide`'s chapter on it for the full reasoning, including the two decisions
made going in: a fresh initial commit here rather than extracted history (simpler, at the cost of
`git blame` not reaching past this repo's first commit), and public visibility (no auth needed for
any downstream consumer or CI to pull it).

Accepted tradeoff, called out before doing this: every engine change is now two commits across two
repos - commit/push/tag here, then a second commit in `sdk-primer-v2` just to bump the submodule
pointer - where before it was one atomic commit touching code, tests, docs and a guide chapter
together. Worth it once real consumers exist; premature before that, which is why this stayed
deferred through the pagination and correlation-ID work that came right before it.
