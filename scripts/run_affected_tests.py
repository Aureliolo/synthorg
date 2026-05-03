#!/usr/bin/env python3
"""Pre-push hook: run only unit tests affected by changed files.

Uses git diff against origin/main to determine which source modules changed,
then maps them to their corresponding test directories via the project's 1:1
``src/synthorg/<module>/`` -> ``tests/unit/<module>/`` layout.
Only Python (``.py``) file changes are considered; non-Python changes are ignored.

Foundational modules (core, config, observability) are imported by nearly every
other module, so changes to them trigger a full test run. Same for any
``conftest.py`` and top-level source files (``__init__.py``, ``constants.py``).

When the affected-tests run goes green, an isolation regression gate runs
``pytest --count 2 -x`` over the same selection (issue #1713). Catches a
new module-level-state offender the moment it lands, before push. Set
``SYNTHORG_SKIP_ISOLATION_GATE=1`` to bypass for emergency pushes (the
primary run still gates on first-run failures regardless).

Exit codes match pytest: 0 (passed/nothing to run), 1 (failures), etc.
Git command failures fall back to running the full unit suite.
"""

import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Make ``tests.baselines.loader`` importable when this script runs from
# the command line (the script's own directory is on ``sys.path`` but
# the repo root, which contains the ``tests`` package, is not).  Both
# this script and ``tests/conftest.py`` use the same loader to keep the
# baseline-validation contract identical across pre-push and pytest.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.baselines.loader import (  # noqa: E402
    BaselineSnapshot as _BaselineSnapshot,
)
from tests.baselines.loader import (  # noqa: E402
    load_baseline_snapshot as _shared_load_baseline_snapshot,
)

# Modules imported by nearly everything -- changes here mean "run all tests".
_BLAST_RADIUS_MODULES = frozenset({"core", "config", "observability"})

# Top-level source files that aren't in a module directory.
_TOP_LEVEL_SRC = frozenset({"__init__.py", "constants.py"})

# Operator opt-out for the isolation regression gate. Set to "1" to skip
# the second --count 2 pass on a single push; the primary run still
# enforces first-run correctness regardless. Documented in the module
# docstring so emergency-push semantics are discoverable.
_SKIP_ISOLATION_GATE_ENV = "SYNTHORG_SKIP_ISOLATION_GATE"

# Minimum path depth for src/synthorg/<module> or tests/unit/<module>.
_MIN_MODULE_DEPTH = 3

# Valid Python package directory names (prevents path traversal).
_SAFE_MODULE_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


class _GitError(Exception):
    """Raised when a required git command fails."""


def _git(*args: str) -> str:
    """Run a git command and return stripped stdout.

    Raises ``_GitError`` on non-zero exit so callers fail closed.
    """
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        check=False,
    )
    if result.returncode != 0:
        msg = f"git {' '.join(args)} failed: {result.stderr.strip()}"
        raise _GitError(msg)
    return result.stdout.strip()


def _merge_base() -> str:
    """Find the merge base between HEAD and origin/main."""
    try:
        return _git("merge-base", "HEAD", "origin/main")
    except _GitError:
        # Fallback: if merge-base fails (e.g. origin/main not fetched, or
        # history too shallow), diff against HEAD~1 so we check *something*.
        return _git("rev-parse", "HEAD~1")


def _changed_files(base: str) -> list[str]:
    """Return files changed between *base* and HEAD.

    Includes both committed and uncommitted changes as a safety net.
    """
    committed = _git("diff", "--name-only", f"{base}...HEAD")
    uncommitted = _git("diff", "--name-only", "HEAD")
    all_files: set[str] = set()
    for block in (committed, uncommitted):
        if block:
            all_files.update(block.splitlines())
    return sorted(all_files)


def _classify_path(parts: tuple[str, ...]) -> tuple[str, str | None]:
    """Classify a file path into a category and optional module name.

    Returns ``(category, module)`` where category is one of:
    ``"conftest"``, ``"blast_radius"``, ``"top_level_src"``,
    ``"src_module"``, ``"test_unit"``, ``"other"``.
    """
    if parts[-1] == "conftest.py":
        return "conftest", None

    is_deep_enough = len(parts) >= _MIN_MODULE_DEPTH
    if is_deep_enough and parts[0] == "src" and parts[1] == "synthorg":
        if parts[2] in _TOP_LEVEL_SRC:
            return "top_level_src", None
        if not _SAFE_MODULE_NAME.match(parts[2]):
            return "other", None
        return (
            ("blast_radius", None)
            if parts[2] in _BLAST_RADIUS_MODULES
            else ("src_module", parts[2])
        )

    if is_deep_enough and parts[0] == "tests" and parts[1] == "unit":
        # The regex already rejects dotted names like test_smoke.py and
        # __init__.py, but listing them explicitly documents the intent.
        is_root = (
            not _SAFE_MODULE_NAME.match(parts[2])
            or parts[2] == "test_smoke.py"
            or parts[2] in _TOP_LEVEL_SRC
        )
        return ("test_unit", ".") if is_root else ("test_unit", parts[2])

    return "other", None


def _affected_test_dirs(changed: list[str]) -> tuple[list[str], bool]:
    """Map changed files to test directories.

    Returns ``(test_dirs, run_all)`` where *run_all* is True when a
    blast-radius module or shared infrastructure was touched.
    """
    modules: set[str] = set()

    for filepath in changed:
        parts = PurePosixPath(filepath).parts
        category, module = _classify_path(parts)

        if category in {"conftest", "blast_radius", "top_level_src"}:
            return [], True
        if module is not None:
            modules.add(module)

    # Build test directory paths (only dirs that actually exist).
    test_dirs: list[str] = []
    for mod in sorted(modules):
        if mod == ".":
            smoke = _REPO_ROOT / "tests" / "unit" / "test_smoke.py"
            if smoke.exists():
                test_dirs.append(str(smoke.relative_to(_REPO_ROOT)))
        else:
            test_dir = _REPO_ROOT / "tests" / "unit" / mod
            if test_dir.is_dir():
                test_dirs.append(str(test_dir.relative_to(_REPO_ROOT)))

    return test_dirs, False


_BASELINE_PATH = _REPO_ROOT / "tests" / "baselines" / "unit_timing.json"


_PASSED_COUNT_RE = re.compile(r"(\d+)\s+passed")


def _parse_test_count(pytest_output: str) -> int | None:
    """Extract the number of passed tests from pytest's final summary.

    Returns ``None`` when the summary line cannot be parsed (degraded
    output, unexpected failure mode, etc.) -- the caller falls back
    to the absolute-seconds rail in that case.
    """
    # pytest prints the summary on the final non-empty line, e.g.
    # ``23373 passed, 16 skipped in 91.86s (0:01:31)``.
    for line in reversed(pytest_output.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        match = _PASSED_COUNT_RE.search(stripped)
        if match:
            return int(match.group(1))
    return None


def _load_baseline_snapshot() -> _BaselineSnapshot | None:
    """Thin wrapper around :func:`tests.baselines.loader.load_baseline_snapshot`.

    Centralised in ``tests/baselines/loader.py`` so the contract stays
    identical between this script (pre-push) and
    ``tests/conftest.py::pytest_sessionfinish`` (regression banner).

    Returns ``None`` only when the baseline file does not exist; a
    malformed baseline propagates :class:`BaselineMalformedError` so
    the operator fixes the typo instead of silently pushing without
    the regression rail.
    """
    return _shared_load_baseline_snapshot(_BASELINE_PATH)


def _parse_env_override() -> float | None:
    """Return the ``UNIT_SUITE_MAX_SECONDS`` override if usable.

    Silently swallowing a typo (``UNIT_SUITE_MAX_SECONDS=3oo``) would
    mean the guard runs with the baseline tolerance while the operator
    thinks they have relaxed it, so malformed values print a diagnostic
    to stderr before falling back.
    """
    env_override = os.environ.get("UNIT_SUITE_MAX_SECONDS")
    if env_override is None:
        return None
    try:
        parsed = float(env_override)
    except ValueError:
        print(
            f"run_affected_tests: UNIT_SUITE_MAX_SECONDS="
            f"{env_override!r} is not a valid float; ignoring "
            f"the override and using the baseline tolerance.",
            file=sys.stderr,
        )
        return None
    # ``float("nan")`` / ``float("inf")`` parse cleanly but make the
    # guard meaningless (every elapsed comparison is False for NaN;
    # Inf disables the cap entirely). A zero or negative cap would
    # block every run. Ignore these and fall back.
    if not math.isfinite(parsed) or parsed <= 0:
        print(
            f"run_affected_tests: UNIT_SUITE_MAX_SECONDS="
            f"{env_override!r} must be a finite positive "
            f"number; ignoring the override and using the "
            f"baseline tolerance.",
            file=sys.stderr,
        )
        return None
    return parsed


def _print_regression_banner(message: str) -> None:
    """Emit a regression banner with the standard footer.

    Keeping the banner footer (run A/B; do not delete tests; update
    baseline intentionally) in one place keeps every failure mode's
    remediation identical without repeating the boilerplate at each
    call site.
    """
    border = "!" * 60
    print(
        f"\n{border}\n"
        f"{message}\n"
        f"Run A/B against origin/main before fixing anything.\n"
        f"Do NOT delete tests or use --no-verify.\n"
        f"If the new baseline is intentional, update "
        f"tests/baselines/unit_timing.json.\n"
        f"{border}",
        file=sys.stderr,
    )


def _check_per_test_regression(
    elapsed: float,
    *,
    snapshot: _BaselineSnapshot,
    test_count: int | None,
) -> bool:
    """Per-test cost rail (the only data-driven rail).

    Returns ``True`` when current per-test cost exceeds
    ``baseline_per_test * threshold_ratio``.  Returns ``False`` when
    we cannot compute current per-test cost (no test count from
    pytest).

    A missing test count is intentionally not surfaced as a regression:
    treating "we could not measure" as "we regressed" would block runs
    on transient pytest output anomalies (e.g. xdist worker crashes
    that swallow the summary line) where there is no actual slowdown
    signal.  The env-cap rail (``UNIT_SUITE_MAX_SECONDS``) still
    catches absolute blow-ups in that path, so the operator escape
    hatch covers the worst case while routine misses degrade gracefully.
    """
    if test_count is None or test_count <= 0:
        return False
    current_per_test_ms = elapsed * 1000.0 / test_count
    max_per_test_ms = snapshot.per_test_ms * snapshot.threshold_ratio
    if current_per_test_ms <= max_per_test_ms:
        return False
    baseline_count_label = str(snapshot.baseline_test_count)
    _print_regression_banner(
        f"REGRESSION DETECTED: per-test cost {current_per_test_ms:.2f}ms "
        f"exceeds {max_per_test_ms:.2f}ms "
        f"(baseline {snapshot.per_test_ms:.2f}ms, "
        f"ratio {snapshot.threshold_ratio:.2f}).\n"
        f"Suite: {elapsed:.0f}s across {test_count} tests "
        f"(baseline test count: {baseline_count_label}).",
    )
    return True


def _check_env_cap(elapsed: float, *, env_max_allowed: float | None) -> bool:
    """Env-cap hard rail.

    If the operator set an absolute ceiling (``UNIT_SUITE_MAX_SECONDS``)
    and we blew past it, fail regardless of the per-test ratio. The
    per-test branch still catches regressions within the cap.
    """
    if env_max_allowed is None or elapsed <= env_max_allowed:
        return False
    _print_regression_banner(
        f"REGRESSION DETECTED: suite took {elapsed:.0f}s, exceeds "
        f"UNIT_SUITE_MAX_SECONDS={env_max_allowed:.0f}s.",
    )
    return True


def _check_timing_regression(
    elapsed: float,
    *,
    run_all: bool,
    test_count: int | None,
) -> bool:
    """Return ``True`` when the run shows a timing regression.

    Only checks full-suite runs (``run_all=True``); affected-only runs
    vary widely and are not comparable to the baseline.  Two rails:

    * ``_check_env_cap`` -- operator escape hatch
      (``UNIT_SUITE_MAX_SECONDS``); blow past it and fail regardless.
    * ``_check_per_test_regression`` -- the data-driven rail.  Per-test
      cost in milliseconds, computed live from elapsed seconds and
      pytest's collected count.  Mechanical test-count growth (PRs
      adding new tests) does not move this metric, so the baseline
      stays valid until per-test cost actually drifts.
    """
    if not run_all:
        return False
    snapshot = _load_baseline_snapshot()
    if snapshot is None:
        return False
    env_max_allowed = _parse_env_override()
    if _check_env_cap(elapsed, env_max_allowed=env_max_allowed):
        return True
    return _check_per_test_regression(
        elapsed,
        snapshot=snapshot,
        test_count=test_count,
    )


def _stream_pytest(cmd: list[str]) -> tuple[int, str]:
    """Run *cmd* as pytest, tee stdout, and return ``(returncode, stdout)``.

    Streams pytest stdout line-by-line so users see live progress
    (``subprocess.run`` + ``capture_output`` buffers everything until
    the process exits, which hides the ~90s full suite behind silence).
    We still tee into a buffer so the "N passed" summary line is
    available for the per-test regression rail.
    """
    with subprocess.Popen(
        cmd,
        cwd=_REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    ) as proc:
        stdout_lines: list[str] = []
        if proc.stdout is None:
            # subprocess.Popen with stdout=PIPE guarantees a pipe; this
            # branch would only fire if the Popen construction itself
            # silently produced no pipe handle, which indicates a
            # platform-level failure worth surfacing rather than
            # silencing with an assert.
            returncode = proc.wait()
            return returncode, ""
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            stdout_lines.append(line)
        returncode = proc.wait()
    return returncode, "".join(stdout_lines)


def _run_pytest(paths: list[str], *, run_all: bool = False) -> int:
    """Run pytest with the given paths.

    Inherits ``--dist loadfile`` from pyproject.toml's ``addopts`` so
    every test in a file stays on the same xdist worker; this prevents
    the cumulative resource leak that crashed workers under the prior
    ``worksteal`` default on Python 3.14 + Windows.

    ``--max-worker-restart=0`` disables worker restarts to avoid a
    known xdist scheduler KeyError when the scheduler tries to
    reassign work to a restarted worker with a new id.
    """
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *paths,
        "-m",
        "unit",
        "-n",
        "8",
        "--max-worker-restart=0",
        "-q",
    ]
    start = time.monotonic()
    returncode, captured_stdout = _stream_pytest(cmd)
    elapsed = time.monotonic() - start
    test_count = _parse_test_count(captured_stdout)
    # Skip the regression guard when tests failed / crashed: worker
    # crashes skew ``elapsed / test_count`` upward (time spent before
    # the crash is charged against the surviving test count) and
    # produce false-positive regressions. The underlying failure is
    # already surfaced via ``returncode`` and the test output. When
    # tests fail the operator needs to fix those first; flipping the
    # regression banner on top of a crash output adds noise without
    # pointing at the real root cause.
    if returncode == 0 and _check_timing_regression(
        elapsed,
        run_all=run_all,
        test_count=test_count,
    ):
        # Regression detected -- block the push even if tests passed.
        return max(returncode, 1)
    return returncode


def _run_isolation_gate(paths: list[str]) -> int:
    """Run ``pytest --count 2 -x`` over the given paths.

    Catches module-level-state isolation regressions (issue #1713) by
    re-running each test exactly once and stopping on the first
    failure. A test that passes the primary run but fails the second
    run almost always means a fixture leaked process-global state that
    polluted the second invocation.

    Skipped when ``SYNTHORG_SKIP_ISOLATION_GATE=1`` (operator escape
    hatch for emergency pushes; the primary run still enforces
    first-run correctness regardless). Skipped when ``paths`` is empty.

    Returns 0 on green or skip, non-zero on regression detected.
    """
    if os.environ.get(_SKIP_ISOLATION_GATE_ENV) == "1":
        print(
            f"Isolation gate skipped via {_SKIP_ISOLATION_GATE_ENV}=1.",
            file=sys.stderr,
        )
        return 0
    if not paths:
        return 0
    print("Isolation gate: re-running affected tests under --count 2 -x...")
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *paths,
        "-m",
        "unit",
        "-n",
        "8",
        "--max-worker-restart=0",
        "--count",
        "2",
        "-x",
        "-q",
    ]
    returncode, _ = _stream_pytest(cmd)
    if returncode != 0:
        border = "!" * 60
        print(
            f"\n{border}\n"
            "ISOLATION REGRESSION: a test passed once but failed on repeat.\n"
            "Module-level state likely leaked across the two invocations.\n"
            "See issue #1713 for the canonical offenders + fix patterns.\n"
            f"Override (single push, not durable): {_SKIP_ISOLATION_GATE_ENV}=1\n"
            f"{border}",
            file=sys.stderr,
        )
    return returncode


def main() -> int:
    """Entry point."""
    try:
        base = _merge_base()
    except _GitError as exc:
        print(f"ERROR: {exc} -- running full unit suite", file=sys.stderr)
        return _run_pytest(["tests/unit/"], run_all=True)

    try:
        changed = _changed_files(base)
    except _GitError as exc:
        print(f"ERROR: {exc} -- running full unit suite", file=sys.stderr)
        return _run_pytest(["tests/unit/"], run_all=True)

    # Filter to Python files only.
    py_changed = [f for f in changed if f.endswith(".py")]
    if not py_changed:
        print("No Python files changed -- skipping unit tests.")
        return 0

    test_dirs, run_all = _affected_test_dirs(py_changed)

    if run_all:
        print("Foundational module or conftest changed -- running full unit suite.")
        # Full-suite runs skip the isolation gate: doubling a multi-minute
        # full-suite run gates a routine push on a 5+ minute extra wait,
        # and the affected-test gate already covers the realistic delta
        # surface. The isolation contract is enforced through targeted
        # runs in active development, not by re-running the world.
        return _run_pytest(["tests/unit/"], run_all=True)

    if not test_dirs:
        print("Changed files don't map to any test directories -- skipping.")
        return 0

    print(f"Running affected tests: {', '.join(test_dirs)}")
    primary_returncode = _run_pytest(test_dirs)
    return (
        primary_returncode
        if primary_returncode != 0
        else _run_isolation_gate(test_dirs)
    )


if __name__ == "__main__":
    sys.exit(main())
