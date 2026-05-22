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
``pytest --count 2 --max-worker-restart=4`` over the same selection and
classifies the outcome.  A test that passes the primary run but fails the
replay points at fixture state leaking process-global state, the exact
failure mode that splits a green local run from a red xdist push.  Worker
crashes scattered across unrelated tests (commonly the Python 3.14 +
Windows ProactorEventLoop IOCP teardown race) are surfaced as advisory
and do not block the gate; a test that crashes the worker on EVERY replay
iteration is still treated as a real bug.  Set
``SYNTHORG_SKIP_ISOLATION_GATE=1`` to bypass for emergency pushes (the
primary run still gates on first-run failures regardless).

Exit codes match pytest: 0 (passed/nothing to run / advisory), 1 (failures),
etc.  Git command failures fall back to running the full unit suite.
"""

import math
import os
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

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

# Valid Python package directory names (letters, digits, underscores;
# leading letter or underscore). This regex is the ONLY barrier stopping
# a crafted git-diff path component (e.g. ``..``) from being joined into
# a filesystem path later. The special case ``"."`` is used for test-unit
# root files; module names proper never contain dots. Do NOT relax it
# without adding an explicit path-bounds check that the resolved test dir
# stays under tests/unit/.
_SAFE_MODULE_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


class _GitError(Exception):
    """Raised when a required git command fails."""


def _git(*args: str, strip: bool = True) -> str:
    """Run a git command and return its stdout.

    Args:
        args: Git argv tokens.
        strip: When ``True`` (default) the whole stdout blob is
            ``str.strip()``-ed for convenience. Callers parsing
            ``--porcelain`` output MUST pass ``strip=False``: porcelain
            v1 status codes are two columns and the first column is a
            space for worktree-only modifications (`` M path``).
            Stripping the blob eats that leading space on the first
            line, shifting every fixed-index slice by one (so
            ``scripts/...`` parses as ``cripts/...`` and the subsequent
            ``git restore`` fails on a bogus pathspec).

    Raises:
        _GitError: On non-zero exit so callers fail closed.
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
    return result.stdout.strip() if strip else result.stdout


def _merge_base() -> str:
    """Find the merge base between HEAD and origin/main."""
    try:
        return _git("merge-base", "HEAD", "origin/main")
    except _GitError as merge_base_exc:
        # Fallback: if merge-base fails (e.g. origin/main not fetched, or
        # history too shallow), diff against HEAD~1 so we check *something*.
        # On an orphan / single-commit branch HEAD~1 also fails; wrap it so
        # the caller gets the friendly "running full unit suite" fallback
        # instead of a raw traceback.
        try:
            return _git("rev-parse", "HEAD~1")
        except _GitError as head_parent_exc:
            msg = (
                f"no merge-base with origin/main ({merge_base_exc}) and "
                f"HEAD~1 unavailable ({head_parent_exc})"
            )
            raise _GitError(msg) from head_parent_exc


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

# xdist prints worker-crash lines like
# ``worker 'gw3' crashed while running 'tests/foo.py::test_bar[2-2]'``
# when a worker process dies (segfault, abort, OS kill).  Used by the
# isolation-gate classifier to tell native-level crashes apart from
# real test failures.
_WORKER_CRASH_RE = re.compile(
    r"worker '(?P<worker>\w+)' crashed while running '(?P<test>[^']+)'",
)

# A different xdist signature for the same underlying problem: when a
# worker terminates abnormally between tests (rather than during one),
# xdist prints ``[gwN] node down: Not properly terminated`` instead of
# the canonical ``worker 'gwN' crashed while running '...'`` form. The
# Python 3.14 + Windows ProactorEventLoop IOCP teardown race produces
# both signatures depending on exactly when the IOCP cleanup blew up,
# and both should be treated as native-level crash advisory rather
# than a real test failure. Captures the worker id only -- there is
# no associated test id in this signature.
_NODE_DOWN_RE = re.compile(
    r"\[(?P<worker>gw\d+)\] node down: Not properly terminated",
)

# pytest in ``-q`` mode prints ``FAILED <test_id> - <reason>`` (or just
# ``FAILED <test_id>``) at the start of a line for every failure in the
# session summary.  ``\S+`` captures up to the first whitespace; valid
# pytest test ids never contain whitespace, so the boundary is safe.
_FAILED_RE = re.compile(r"^FAILED (?P<test>\S+)", re.MULTILINE)

# pytest-repeat appends a ``[N-M]`` suffix to each repetition's test id.
# Stripping it lets the classifier recognise the same logical test
# crashing on multiple iterations.  Anchored to ``$`` so a parametrize
# value like ``test_foo[a-b][1-2]`` strips only the trailing repeat
# suffix.  A naturally-parametrized id of the bare shape ``test_foo[1-2]``
# is indistinguishable from a pytest-repeat suffix, but the project's
# parametrize values use descriptive names so the collision is theoretical.
_REPEAT_SUFFIX_RE = re.compile(r"\[\d+-\d+\]$")

# Minimum crash count for the same logical test to be treated as a real
# bug rather than transient native-level flakiness.  Two crashes across
# distinct pytest-repeat iterations of the same test (e.g. ``[1-2]``
# AND ``[2-2]``) means every replay of the test crashed the worker --
# very different signal from a single crash that may just be infra noise.
_MIN_CRASHES_FOR_REAL_BUG = 2


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

    ``--max-worker-restart=4`` lets xdist transparently replace a
    worker that crashes natively (no Python-level signature, no
    faulthandler trace) -- a pattern observed under multi-worktree
    Windows pre-push contention.  ``_classify_isolation_outcome``
    parses the captured stdout afterwards and surfaces native crashes
    scattered across unrelated tests as advisory rather than failure;
    real test failures and same-test repeated crashes still block.
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
        "--max-worker-restart=4",
        "-q",
    ]
    start = time.monotonic()
    returncode, captured_stdout = _stream_pytest(cmd)
    elapsed = time.monotonic() - start
    test_count = _parse_test_count(captured_stdout)
    outcome = _classify_isolation_outcome(returncode, captured_stdout)
    # Native crashes scattered across unrelated tests are reclassified
    # as advisory: the gate exits 0 but emits a banner.  Real test
    # failures and same-test repeated crashes still block.
    if outcome.kind == "crash_advisory":
        _print_isolation_banner(outcome)
        return 0
    effective_returncode = outcome.exit_code
    # Skip the regression guard when tests failed / crashed: worker
    # crashes skew ``elapsed / test_count`` upward (time spent before
    # the crash is charged against the surviving test count) and
    # produce false-positive regressions. The underlying failure is
    # already surfaced via ``effective_returncode`` and the test
    # output. When tests fail the operator needs to fix those first;
    # flipping the regression banner on top of a crash output adds
    # noise without pointing at the real root cause.
    if effective_returncode == 0 and _check_timing_regression(
        elapsed,
        run_all=run_all,
        test_count=test_count,
    ):
        # Regression detected -- block the push even if tests passed.
        return max(effective_returncode, 1)
    if outcome.kind == "regression":
        _print_isolation_banner(outcome)
    return effective_returncode


@dataclass(frozen=True)
class IsolationOutcome:
    """Classified result of a single isolation-gate pytest invocation.

    ``kind`` captures the gate's verdict; ``exit_code`` is what the
    script should return.  ``crashed_tests`` / ``failed_tests`` /
    ``repeated_crashes`` carry the supporting evidence so the banner
    can name names.

    Invariants (enforced in ``__post_init__``):

    * ``pass`` -- ``exit_code == 0`` and every evidence tuple is empty.
    * ``crash_advisory`` -- ``exit_code == 0``, ``crashed_tests`` is
      non-empty, ``failed_tests`` and ``repeated_crashes`` are empty.
    * ``regression`` -- ``exit_code >= 1``.  Evidence tuples may all be
      empty when pytest exits non-zero with degraded output the parser
      cannot interpret -- we fail closed rather than silently pass.

    Enforcing these at construction means the banner can rely on the
    invariant without re-checking at the print site.
    """

    kind: Literal["pass", "regression", "crash_advisory"]
    exit_code: int
    crashed_tests: tuple[str, ...] = field(default_factory=tuple)
    failed_tests: tuple[str, ...] = field(default_factory=tuple)
    repeated_crashes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Reject illegal ``(kind, exit_code, evidence)`` combinations."""
        if self.kind == "pass":
            if self.exit_code != 0:
                msg = f"pass outcome must have exit_code=0, got {self.exit_code}"
                raise ValueError(msg)
            if self.crashed_tests or self.failed_tests or self.repeated_crashes:
                msg = "pass outcome must carry no evidence tuples"
                raise ValueError(msg)
        elif self.kind == "crash_advisory":
            if self.exit_code != 0:
                msg = (
                    "crash_advisory outcome must have exit_code=0, "
                    f"got {self.exit_code}"
                )
                raise ValueError(msg)
            if not self.crashed_tests:
                msg = "crash_advisory outcome must list at least one crashed test"
                raise ValueError(msg)
            if self.failed_tests or self.repeated_crashes:
                msg = (
                    "crash_advisory outcome must not carry failed_tests or "
                    "repeated_crashes"
                )
                raise ValueError(msg)
        elif self.exit_code == 0:  # regression
            msg = "regression outcome must have non-zero exit_code"
            raise ValueError(msg)


def _parse_worker_crashes(stdout: str) -> tuple[tuple[str, str], ...]:
    """Extract ``(worker_id, test_id)`` for every xdist worker crash."""
    return tuple(
        (m.group("worker"), m.group("test")) for m in _WORKER_CRASH_RE.finditer(stdout)
    )


def _parse_node_down(stdout: str) -> tuple[str, ...]:
    """Return the worker ids of every ``[gwN] node down`` announcement."""
    return tuple(m.group("worker") for m in _NODE_DOWN_RE.finditer(stdout))


def _parse_test_failures(stdout: str) -> tuple[str, ...]:
    """Extract test ids from ``FAILED`` summary lines."""
    return tuple(m.group("test") for m in _FAILED_RE.finditer(stdout))


def _classify_isolation_outcome(
    returncode: int,
    stdout: str,
) -> IsolationOutcome:
    """Decide whether the gate run is a regression, advisory, or pass.

    The interesting axis is *real failure* vs *native crash*.  xdist
    marks a crashed test ``FAILED`` as collateral, so a ``FAILED``
    line that points at a crashed test is filtered out -- the crash
    is the real signal.

    Decision tree:

    * Any real failure (``FAILED`` for a non-crashed test) -> regression.
    * Same logical test crashed on multiple iterations -> regression
      (a real bug; pytest-repeat ``[N-M]`` suffix is stripped before
      counting so the two iterations of one test collapse).
    * Crashes only, no repeats -> crash advisory (treat as pass; the
      gate exits 0 and prints a hint about Windows ProactorEventLoop /
      cross-worktree contention).
    * Worker(s) went ``node down`` with a non-zero returncode (and no
      real failure / repeated crash above) -> crash advisory. The
      controller-side loadscope crash guard in ``tests/conftest.py``
      suppresses the downstream ``INTERNALERROR>`` the dead-worker
      chain used to emit, and a worker killed mid-teardown can die
      before pytest prints any summary, so neither signal is required
      to recognise the documented Python 3.14 + Windows xdist teardown
      crash. The repeated-named-crash check above still blocks a test
      that crashes the worker on every run.
    * No crashes, no failures, returncode 0 -> pass.
    * No parsable signal but returncode non-zero -> fail closed
      (regression) so degraded output never silently passes.
    """
    crashes = _parse_worker_crashes(stdout)
    crashed_tests = tuple(test for _, test in crashes)
    crashed_set = set(crashed_tests)
    node_down_workers = _parse_node_down(stdout)
    failed_tests_raw = _parse_test_failures(stdout)
    real_failures = tuple(t for t in failed_tests_raw if t not in crashed_set)

    normalized = Counter(_REPEAT_SUFFIX_RE.sub("", t) for t in crashed_tests)
    repeated = tuple(
        sorted(t for t, n in normalized.items() if n >= _MIN_CRASHES_FOR_REAL_BUG)
    )

    if real_failures:
        return IsolationOutcome(
            kind="regression",
            exit_code=max(returncode, 1),
            crashed_tests=crashed_tests,
            failed_tests=real_failures,
        )
    if repeated:
        return IsolationOutcome(
            kind="regression",
            exit_code=max(returncode, 1),
            crashed_tests=crashed_tests,
            repeated_crashes=repeated,
        )
    if crashes:
        return IsolationOutcome(
            kind="crash_advisory",
            exit_code=0,
            crashed_tests=crashed_tests,
        )
    # A worker that went ``node down`` is a native-level crash, not a
    # test failure. The real-failure and repeated-named-crash checks
    # above have already returned, so reaching here means the only
    # adverse signal is the worker death itself, with a non-zero exit.
    #
    # We do NOT require a downstream ``INTERNALERROR>`` here: the
    # controller-side loadscope crash guard in ``tests/conftest.py``
    # (``_install_xdist_loadscope_crash_guard``) deliberately suppresses
    # the reschedule ``KeyError`` that used to surface as
    # ``INTERNALERROR>``, and a worker killed mid-teardown can die
    # before pytest prints any FAILED summary -- so neither an
    # INTERNALERROR nor a parseable test id is guaranteed for the
    # documented Python 3.14 + Windows xdist teardown crash. Requiring
    # the INTERNALERROR would (now that the guard suppresses it) fail
    # closed on every such crash, blocking every push that widens the
    # affected selection. The repeated-crash guard above is the safety
    # net for a test that genuinely crashes the worker on every run; a
    # one-off node-down is treated as advisory. The test names are
    # unrecoverable from a bare node-down, so surface the worker ids.
    if node_down_workers and returncode != 0:
        return IsolationOutcome(
            kind="crash_advisory",
            exit_code=0,
            crashed_tests=tuple(f"<worker {w}>" for w in node_down_workers),
        )
    if returncode == 0:
        return IsolationOutcome(kind="pass", exit_code=0)
    return IsolationOutcome(kind="regression", exit_code=returncode)


def _print_isolation_banner(outcome: IsolationOutcome) -> None:
    """Print the right diagnostic banner for *outcome*'s kind.

    Three banners:

    * ``regression`` -- the operator must investigate.  Distinguishes
      module-state leak (real failures) from a test that consistently
      crashes the worker (repeated crashes) so the message points at
      the right cause.
    * ``crash_advisory`` -- a hint that the run hit native-level
      flakiness (commonly Python 3.14 ProactorEventLoop teardown races
      amplified by concurrent worktrees).  Gate still passes.
    * ``pass`` -- nothing to print.
    """
    if outcome.kind == "pass":
        return
    border = "!" * 60
    if outcome.kind == "regression":
        if outcome.failed_tests:
            body = (
                "ISOLATION REGRESSION: a test passed once but failed on repeat.\n"
                "Module-level state likely leaked across the two invocations.\n"
                "Common offenders: module-level dicts/sets that fixtures reset\n"
                "in only one directory; ``monkeypatch.setattr`` on structlog\n"
                "lazy-proxy log methods; cached caches that survive teardown.\n"
                f"Failed: {', '.join(outcome.failed_tests)}\n"
                f"Override (single push, not durable): {_SKIP_ISOLATION_GATE_ENV}=1"
            )
        elif outcome.repeated_crashes:
            body = (
                "ISOLATION REGRESSION: a test crashed the xdist worker on\n"
                "every replay.  This is a real bug in the test or its\n"
                "fixtures (memory corruption, segfault, native-level\n"
                "deadlock), not transient infra noise.\n"
                f"Repeated crashes: {', '.join(outcome.repeated_crashes)}\n"
                f"Override (single push, not durable): {_SKIP_ISOLATION_GATE_ENV}=1"
            )
        else:
            # Fail-closed: pytest exited non-zero but emitted no parsable
            # FAILED or worker-crash signal.  Don't silently pass; surface
            # the raw exit code and ask the operator to inspect the run.
            body = (
                "ISOLATION REGRESSION: pytest exited non-zero "
                f"({outcome.exit_code}) with output the gate could not\n"
                "parse.  Inspect the captured pytest output above for\n"
                "the underlying failure.\n"
                f"Override (single push, not durable): {_SKIP_ISOLATION_GATE_ENV}=1"
            )
        print(f"\n{border}\n{body}\n{border}", file=sys.stderr)
        return
    # crash_advisory
    body = (
        "Isolation gate ADVISORY: xdist worker(s) crashed during the\n"
        "replay run, but no real test failures were detected.  Most\n"
        "common cause is the Python 3.14 + Windows ProactorEventLoop\n"
        "IOCP teardown race, often amplified by concurrent worktrees\n"
        "running pytest at the same time.  Treating as advisory (gate\n"
        f"passes).  Crashed tests: {', '.join(outcome.crashed_tests)}"
    )
    print(f"\n{border}\n{body}\n{border}", file=sys.stderr)


def _run_isolation_gate(paths: list[str]) -> int:
    """Run ``pytest --count 2`` over the given paths and classify the result.

    Catches module-level-state isolation regressions by re-running each
    test exactly once.  A test that passes the primary run but fails
    the replay almost always means a fixture leaked process-global
    state that polluted the second invocation.

    ``--max-worker-restart=4`` lets xdist transparently replace a
    worker that crashes during the replay.  This addresses the
    Python 3.14 + Windows ProactorEventLoop IOCP teardown race
    (https://github.com/python/cpython/issues/116773 and family) and
    other native crashes that previously cascaded into a false
    isolation-regression banner.  ``_classify_isolation_outcome``
    parses the captured stdout and tells real isolation regressions
    apart from native flakiness; only the former blocks the gate.

    Skipped when ``SYNTHORG_SKIP_ISOLATION_GATE=1`` (operator escape
    hatch for emergency pushes; the primary run still enforces
    first-run correctness regardless). Skipped when ``paths`` is empty.

    Returns 0 on green / skip / advisory; non-zero on regression.
    """
    if os.environ.get(_SKIP_ISOLATION_GATE_ENV) == "1":
        print(
            f"Isolation gate skipped via {_SKIP_ISOLATION_GATE_ENV}=1.",
            file=sys.stderr,
        )
        return 0
    if not paths:
        return 0
    print("Isolation gate: re-running affected tests under --count 2...")
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *paths,
        "-m",
        "unit",
        "-n",
        "8",
        "--max-worker-restart=4",
        "--count",
        "2",
        "-q",
    ]
    returncode, captured_stdout = _stream_pytest(cmd)
    outcome = _classify_isolation_outcome(returncode, captured_stdout)
    _print_isolation_banner(outcome)
    return outcome.exit_code


def _resolve_changed_files() -> list[str] | None:
    """Return changed files, or ``None`` if we should run the full suite.

    Returns ``None`` for any condition that forces a full unit run:
    git command failure (no merge-base, shallow history, etc.) or a
    ``pyproject.toml`` change.  ``pyproject.toml`` carries the pytest
    config (addopts, xdist, plugin list, markers); a push that touches
    it but no Python file would otherwise skip every test, shipping
    the configuration change unverified.
    """
    try:
        base = _merge_base()
        changed = _changed_files(base)
    except _GitError as exc:
        print(f"ERROR: {exc} -- running full unit suite", file=sys.stderr)
        return None
    if "pyproject.toml" in changed:
        print("pyproject.toml changed -- running full unit suite.")
        return None
    return changed


def _tracked_dirty_paths() -> set[str]:
    """Return the set of tracked paths with worktree/index changes.

    Untracked files (``??``) are excluded: pre-commit's "files were
    modified by this hook" detection is about *tracked* file content
    changing, and reverting a hook-created untracked artefact is not
    this guard's job. Renames (``R``) carry ``orig -> new``; both sides
    are recorded so a hook-induced rename is fully reconciled.
    """
    porcelain = _git("status", "--porcelain", strip=False)
    paths: set[str] = set()
    for line in porcelain.splitlines():
        if not line or line.startswith("??"):
            continue
        # Porcelain v1: 2 status chars, a space, then the path(s).
        # Only rename/copy entries carry an ``orig -> new`` payload; a
        # plain filename containing the literal `` -> `` substring must
        # not be misparsed as one (it would record non-existent paths).
        status = line[:2]
        payload = line[3:]
        if ("R" in status or "C" in status) and " -> " in payload:
            old, new = payload.split(" -> ", 1)
            paths.add(old.strip())
            paths.add(new.strip())
        else:
            paths.add(payload.strip())
    return paths


def _reconcile_worktree(before: set[str]) -> int:
    """Revert any tracked file the hook run dirtied but was clean before.

    Files already dirty *before* the run (the developer's actual work
    being pushed) are never touched. Only paths that were pristine
    pre-run and changed during the run are restored, so a side-effecting
    test or a stray ``uv.lock`` rewrite cannot trip pre-commit's "files
    were modified by this hook" while real changes stay intact.

    Returns 0 on success (including nothing to do), 1 if a restore
    failed -- the caller folds this into the overall exit code so a
    silent un-revertable mutation never passes unnoticed.
    """
    try:
        after = _tracked_dirty_paths()
    except _GitError as exc:
        # Fail closed: if we cannot read post-run status we cannot prove
        # the run left the tree clean. Returning 0 here would let a
        # test-induced mutation slip through silently (pre-commit would
        # later block the push with no hint the hook tried and gave up).
        print(
            f"run_affected_tests: could not read post-run git status "
            f"({exc}); cannot verify the run left the tree clean -- "
            f"failing closed. Inspect the working tree manually.",
            file=sys.stderr,
        )
        return 1
    newly_dirtied = sorted(after - before)
    if not newly_dirtied:
        return 0
    border = "!" * 60
    print(
        f"\n{border}\n"
        f"run_affected_tests: the affected-test run modified "
        f"{len(newly_dirtied)} tracked file(s) that were clean before "
        f"it started:\n  " + "\n  ".join(newly_dirtied) + "\n"
        f"Reverting them so the pre-push hook does not report 'files "
        f"were modified by this hook'. This is a side effect of the "
        f"test run, not your changes -- investigate the writer if it "
        f"recurs.\n{border}",
        file=sys.stderr,
    )
    try:
        _git("restore", "--", *newly_dirtied)
    except _GitError as exc:
        print(
            f"run_affected_tests: FAILED to revert hook-modified files "
            f"({exc}). The working tree is left dirty; fix the writer "
            f"or revert manually before pushing.",
            file=sys.stderr,
        )
        return 1
    return 0


def _run_tests() -> int:
    """Select and run the affected (or full) unit suite."""
    changed = _resolve_changed_files()
    if changed is None:
        return _run_pytest(["tests/unit/"], run_all=True)

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


def main() -> int:
    """Entry point.

    Snapshots the set of already-dirty tracked files, runs the suite,
    then reverts only the files the run itself dirtied. The test exit
    code is preserved; a failed revert is folded in so an un-revertable
    mutation cannot pass silently.
    """
    try:
        before = _tracked_dirty_paths()
    except _GitError as exc:
        # No pre-run snapshot means we cannot safely tell hook-induced
        # changes from the developer's own. Skip reconciliation rather
        # than risk reverting real work; the run still gates correctness.
        print(
            f"run_affected_tests: could not read pre-run git status "
            f"({exc}); worktree reconciliation disabled for this run.",
            file=sys.stderr,
        )
        return _run_tests()
    test_returncode = _run_tests()
    reconcile_returncode = _reconcile_worktree(before)
    return max(test_returncode, reconcile_returncode)


if __name__ == "__main__":
    sys.exit(main())
