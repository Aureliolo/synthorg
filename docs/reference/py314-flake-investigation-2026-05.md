# Python 3.14 CI flake investigation (2026-05-05)

## Outcome

No reproducible Python 3.14-specific flake was identified. The single CI failure
attributed to "test flakiness" on 2026-05-05 was a deterministic edge case in a
newly-added symlink-escape test, not a 3.14 issue. No further test-isolation
tightening is warranted at this time.

## Runs inspected

`gh run list --workflow ci.yml --status failure` for the 2026-05-05 window
returned three failed runs. Only one was on the Python `Test (Python 3.14)` job:

| Run | Branch | Failed test |
| --- | --- | --- |
| 25399305527 | `chore/convention-rollout-gate` | `tests/unit/scripts/test_check_convention_gate_inventory.py::test_gate_path_symlink_escape_treated_as_missing` |
| 25361288425 | `main` | unrelated (post-merge gate run) |
| 25359737418 | `fix/test-isolation-gate-1755` | already a fix PR for cross-loop asyncio primitives |

## Root cause for run 25399305527

The failing assertion was `len(violations) == 1` evaluating to `len([]) == 1`.
The test creates a symlink under the test repo root pointing at a tempdir that is
a sibling of the repo root, then asserts that the convention-gate inventory check
flags the symlink as a missing gate file (because resolved path escapes the repo).

On the Linux GitHub Actions runner that day the symlink resolution *did not*
escape: both `tmp_path_factory.mktemp("outside_repo")` and the test repo root
landed under the same `pytest-of-runner/...` parent, so the resolved real path
was still inside the repo root and the gate file was treated as present.

This is a deterministic environment quirk, not a flake. Subsequent runs of the
same test on identical CI infrastructure pass (the parent-tempdir layout
varies between sessions).

## Already-merged mitigations relevant to 3.14 isolation

The 3.14 + xdist surface area was tightened during the same audit window by
prior PRs:

- `fix(test): exterminate xdist-flaky tests with module-level state (#1713)`:
  removed module-level state leaks that surfaced under `loadfile` distribution.
- `fix(test): pre-push isolation gate flakes from cross-loop asyncio primitives
  (#1755)`: replaced asyncio primitives constructed at import time with
  per-test instances.

The pyproject `addopts` already pin `-n 8 --dist=loadfile` so a Windows + 3.14
+ ProactorEventLoop teardown leak cannot escape into other test modules.
`tests/conftest.py` already enforces a per-unit-test wall-clock budget of 8 s
which surfaces real regressions deterministically rather than as flakes.

## Why no further work is queued

A genuine flake-hunt requires a reproducer. The 2026-05-05 symlink test runs
green on every subsequent CI invocation; rerunning it in a loop on Linux did
not reproduce the empty-violations branch. Adding pre-emptive isolation
hardening without a failing test would be speculative work that could mask
real flakes if they emerge.

If a 3.14-specific flake re-surfaces, the next investigation should:

1. capture the failing run's full junit.xml + stdout artifact;
2. attempt local reproduction with `RUN_INTEGRATION_TESTS=1 uv run python -m
   pytest tests/<failing_path> --count=20`;
3. only then add a targeted isolation fixture or skip-marker.
