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

This engine is released on the same weekly clock as blt and the yoki
SDKs, with the same branch names. `main` holds only released code;
nothing is committed to it directly.

### The week

| Day | What happens |
|---|---|
| Monday | The previous week's release branch is merged to `main` and tagged. The new week's release branch opens from `main`. |
| Monday - Thursday | Work happens on short branches and is merged into the week's release branch as each piece is finished. |
| Thursday, end of day | Cut-off. What is not merged waits for next week. |
| Friday | The release branch is tested as a whole (see below). No new work goes in - only fixes for what the testing finds. |
| Monday | If Friday's tests passed, release. If not, the release is held until they do, or skipped. A week in which nothing was merged has no release. |

### Names

A release is named for the Monday it is released on: `YYYY.MM.Wnn`,
where `nn` counts the Mondays of that month. Work done in the week of
19 October 2026 goes into `release/2026.10.W04` and is released on
Monday 26 October, the fourth Monday of October. So the branch to be on
is always the one named for **next** Monday.

| Thing | Name | Example |
|---|---|---|
| Release branch | `release/<release>` | `release/2026.10.W04` |
| Feature branch | `<release>/F/<What_it_does>` | `2026.10.W04/F/Rate_limit_headers` |
| Bug-fix branch | `<release>/B/<What_it_fixes>` | `2026.10.W04/B/Lock_version_0_1_1` |
| Hotfix branch | `<release>/H/<What_it_fixes>` | `2026.10.W04/H/Retry_after_ignored` |
| Release tag, on `main` | `v<major>.<minor>.<patch>` | `v0.2.0` |

As with the SDKs, and unlike blt, the tag is a version rather than the
week's name: this is a library that other projects pin. A new feature
or option is a minor step (`v0.1.0` to `v0.2.0`), a week of only fixes
is a patch step (`v0.2.0` to `v0.2.1`), and a change that would break a
caller is a major step. The tag's message names the release branch it
came from.

### The loop for one piece of work

```
git checkout release/2026.10.W04 && git pull
git checkout -b 2026.10.W04/F/Rate_limit_headers
# small commits, each with its tests; run the checks before every one
git push -u origin 2026.10.W04/F/Rate_limit_headers     # and push often
git checkout release/2026.10.W04
git merge --no-ff 2026.10.W04/F/Rate_limit_headers
git push
```

- **Commit small and often.** One change or one fix per commit, with
  its tests in the same commit, and the checks passing at each one.
  Push as you go; the branch on GitHub should never be far behind.
- **Feature and bug-fix branches** start from the week's release branch
  and merge back into it.
- **Hotfix branches** are for something a project built on the engine
  cannot wait until Monday for. They start from `main`, merge to `main`
  with a patch tag, and are then merged into the open release branch as
  well.

### Friday's test

By Thursday's cut-off, the release branch already carries the new
version: `version` in `pyproject.toml` set to the coming tag without its
`v`, then `uv lock`, on an `H/` branch like any other change. Both
files go in the same commit - a version set without `uv lock` leaves
the lockfile behind, and the next `uv sync` rewrites it. Friday then
tests what Monday ships, and the tag and the package agree.

On the release branch, all of:

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

### Releasing, on Monday

```
git checkout main && git pull
git merge --no-ff release/2026.10.W04 -m "Release v0.2.0 (2026.10.W04)"
git tag -a v0.2.0 -m "2026.10.W04: what changed, in a line or two"
git push origin main v0.2.0
git checkout -b release/2026.11.W01 && git push -u origin release/2026.11.W01
```

The tag has to be pushed by name, as above: a plain `git push` does not
send tags, and nothing can pin a tag that is not on GitHub.

Projects built on the engine take the new tag on their own release
branch for the following week, when they need it: change the tag for
`sdk-primer` under `[tool.uv.sources]` in their `pyproject.toml`, run
`uv lock`, run their tests, commit. `yoki-sdk-primer` also carries this
repo as a submodule, so it needs a second commit there moving the
submodule to the new tag, alongside the bump in `examples/user-service`.

The same Monday, the branches of any release that went out a month or
more ago come off GitHub (see Tidying up).

### Tidying up

A branch is deleted locally once it has been merged, and from GitHub a
month after the release it went out in.

Locally, straight after the merge:

```
git branch -d 2026.10.W04/F/Rate_limit_headers
```

`git branch -d` refuses to delete a branch that has not been merged
into the branch you are on, which is the check that nothing is lost. A
release branch is deleted locally the same way after its Monday
release; the tag keeps that release reachable.

On GitHub, each release's branches - its work branches and the release
branch itself - stay up for a month after that release, so a week's
work can still be looked at branch by branch while it is recent. They
come off on the first release Monday a month or more after the
release: `2026.10.W04`, released Monday 26 October, has its branches
deleted from GitHub on Monday 30 November, as part of releasing
`2026.11.W05`.

```
git fetch --prune
git branch -r --list 'origin/2026.10.W04/*' 'origin/release/2026.10.W04'
git push origin --delete 2026.10.W04/F/Rate_limit_headers ... release/2026.10.W04
git fetch --prune
```

Only a release that has been tagged is tidied this way. A branch that
was never merged stays until someone decides about it.
