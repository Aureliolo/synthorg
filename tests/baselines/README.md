# Test timing baselines

This directory holds timing baselines used by the unit-suite regression
guard.  Two consumers read it:

- `scripts/run_affected_tests.py::_check_timing_regression` (pre-push hook)
- `tests/conftest.py::pytest_sessionfinish` (warn-and-fail at the end of
  any pytest run that includes the unit suite)

Both compare current run timing against the baseline using
**per-test milliseconds** as the primary metric, and fail the run when
`current_per_test_ms > baseline_per_test_ms * regression_threshold_ratio`.

## Why per-test ms, not absolute seconds

Every PR that lands a few hundred new tests would otherwise trip an
absolute-seconds threshold without representing a real slowdown.  The
old design treated this as a regression and forced operators to
"refresh the baseline" each time, which masked actual per-test
slowdowns under the bigger absolute number.  Per-test cost is
dimension-correct: it normalizes against population size and only
fires when individual tests actually slow down.  The baseline stays
valid until per-test cost drifts.

## Schema (`unit_timing.json`)

| Field | Type | Required | Purpose |
|---|---|---|---|
| `unit_suite_seconds` | number | yes | Wall-clock seconds for the full unit suite at baseline measurement.  Human-readable context; combined with `test_count` to derive `per_test_ms` when the latter is absent. |
| `test_count` | int | yes | Tests collected at baseline measurement.  Required for the partial-run guard in `pytest_sessionfinish` (the check skips when `unit_count < test_count * 0.8`). |
| `per_test_ms` | number | optional | Per-test milliseconds.  When present, used directly; when absent, derived from `unit_suite_seconds * 1000 / test_count`.  Use the directly-stored form when you want to lock the metric independent of the absolute numbers. |
| `regression_threshold_ratio` | number | optional | Ratio cap.  Defaults to `1.3`.  A run trips the guard when `current_per_test_ms > baseline_per_test_ms * regression_threshold_ratio`. |
| `commit` | string | optional | Commit SHA at which the baseline was measured.  Pure documentation. |
| `measured_at` | string | optional | ISO 8601 date the baseline was measured.  Pure documentation. |
| `notes` | string | optional | Human notes about why the baseline was last refreshed.  Pure documentation. |

## Updating the baseline

The PreToolUse hook `scripts/check_no_edit_baseline.sh` blocks Claude
from editing this file; baseline refreshes require explicit user
intent so we do not silently mask regressions.

To refresh the baseline yourself:

1. Check out the exact branch / commit whose source change justifies
   the refresh (the candidate revision, NOT a clean
   `origin/main` checkout).  The numbers must reflect the same code
   the guard will compare against, otherwise `unit_timing.json`
   drifts away from the suite as soon as the change merges.  Then
   run the full unit suite:

   ```bash
   uv run python -m pytest tests/ -m unit -n 8 --durations=0
   ```

2. Note the elapsed wall-clock and the *collected* unit-test count
   that the regression guard uses.  pytest's
   `=== N passed in ... ===` summary undercounts when skips / xfails
   are present; prefer the collection count from `pytest --collect-only`
   or the `_unit_elapsed_secs` accumulator's denominator (the
   `unit_count` computed in `conftest.py::pytest_sessionfinish`).

3. Update `unit_timing.json`:

   - Set `unit_suite_seconds` to the elapsed wall-clock.
   - Set `test_count` to the unit-test collection count (NOT the
     passed count; skipped tests still cost setup time on the next
     run).
   - Update `commit` and `measured_at`.
   - Add a `notes` entry explaining why the refresh was needed (e.g.
     "intentional infrastructure change reduced per-test cost by 12%").

4. Commit with an explicit `chore: refresh unit-timing baseline`
   message that links the source-code change which justifies the
   refresh.  Refreshing the baseline to mask a regression is a
   documented anti-pattern; the regression guard exists precisely
   to catch that.

## Why we removed `regression_threshold_secs`

The previous schema carried an absolute-seconds tolerance
(`regression_threshold_secs: 15`).  It existed so the guard would not
trip on minor runner noise.  Per-test ms is already noise-tolerant
(thermal throttling, parallel mypy on the same machine, etc., move
the absolute number but not the ratio against baseline_per_test).
The absolute field added no signal but caused real damage every time
upstream landed new tests.  Both code consumers now ignore it.

If your live `unit_timing.json` still carries
`regression_threshold_secs`, it is harmless: the field is silently
ignored by the parsers.  You may delete it during the next manual
baseline refresh.  New baseline files should omit it.
