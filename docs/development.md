# Developing sdk-primer

For working on the engine itself: the checks, and the weekly release
cadence. For using it, see the [README](../README.md).

## Checks

```
uv sync
uv run pytest
uv run coverage run -m pytest && uv run coverage report
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

The tests mock HTTP with respx; none needs a live service. Coverage is
at 100% and stays there: a change that lowers it brings its tests.

## Releases

While blt and the yoki SDKs are still being built, there is no weekly
release train: it was paused on 2026-10-29, and comes back once
something outside the project depends on these releases. Work is merged
to `main` as soon as it is finished and CI passes, and the engine gets a
tag when a project needs what changed.

### The loop for one piece of work

```
git checkout main && git pull
git checkout -b F/What_it_does
# small commits, each with its tests; run the checks before every one
git push -u origin F/What_it_does        # and push often: CI runs on every push
git checkout main && git pull
git merge --no-ff F/What_it_does
git push
git branch -d F/What_it_does && git push origin --delete F/What_it_does
```

| Thing | Name | Example |
|---|---|---|
| Feature branch | `F/<What_it_does>` | `F/Job_details` |
| Bug-fix branch | `B/<What_it_fixes>` | `B/Paging_stops_early` |
| Release tag, on `main` | `v<major>.<minor>.<patch>` | `v0.2.0` |

- **Commit small and often.** One change or one fix per commit, with
  its tests in the same commit, and the checks passing at each one.
  Push as you go; the branch on GitHub should never be far behind.
- **`main` always passes.** Merge a branch only once CI is green on it.
- **Delete a branch once it is merged**, locally and on GitHub.
  `git branch -d` refuses a branch that is not merged, which is the
  check that nothing is lost.

### Tagging a release

When a project needs a change that is on `main`, check `main` first:

1. `ruff check`, `ruff format --check`, `mypy`, the unit tests, and
   coverage still at 100%.
2. The tests of every project built on the engine, with its
   `sdk-primer` source pointed at this checkout
   (`sdk-primer = { path = "../sdk-primer-core", editable = true }`
   under `[tool.uv.sources]`, not committed), so a change that breaks
   one of them is found before the tag:
   - `yoki-py-commvault-sdk`
   - `yoki-py-veeam-sdk`
   - `yoki-commvault-lab`
   - `examples/user-service` in `yoki-sdk-primer`

A project that fails step 2 because of a deliberate change here makes
the release a major step, and the tag message says what callers have
to change.

Then set the version and tag it:

```
git checkout -b B/Version_0_3_0
# version in pyproject.toml, without the v; then:
uv lock
git commit -am "Version 0.3.0"
git checkout main && git merge --no-ff B/Version_0_3_0 && git push
git tag -a v0.3.0 -m "What changed, in a line or two"
git push origin v0.3.0
```

The version and `uv lock` go in the same commit: a version set without
it leaves the lockfile behind. The tag has to be pushed by name, as
above: a plain `git push` does not send tags, and nothing can pin a tag
that is not on GitHub. A new call or option is a minor step (`v0.1.0`
to `v0.2.0`), only fixes a patch step (`v0.2.0` to `v0.2.1`), and a
change that would break a caller a major step.

The SDKs and blt then take the new tag: change the tag for `sdk-primer` under
`[tool.uv.sources]` in their `pyproject.toml`, run `uv lock`, run their
tests, commit. Projects pin tags. A pin to a commit is only for a
change still on its way, and is replaced by the tag before that
project's own branch is merged.
