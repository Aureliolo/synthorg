"""Root test configuration and shared fixtures."""

import faulthandler
import logging
import os
import shutil
import subprocess
import sys
import time
from collections.abc import AsyncGenerator, Iterable, Iterator
from pathlib import Path
from typing import Any

# Boot-time guard parity (see synthorg.api.app create_app): every backend
# boot -- dev, pre-release, prod -- refuses to start with an ephemeral
# pagination cursor secret. Tests build the app via create_app() so they
# need a stable value too. Set at module import (before any collection
# imports cascade through to create_app) and only when not already set,
# so tests that explicitly drive the env var still control it.
os.environ.setdefault(
    "SYNTHORG_PAGINATION_CURSOR_SECRET",
    "test-suite-stable-cursor-secret-not-a-real-secret",
)

import aiosqlite
import pytest
import structlog
from hypothesis import HealthCheck, settings
from hypothesis.database import (
    DirectoryBasedExampleDatabase,
    ExampleDatabase,
    MultiplexedDatabase,
)

from synthorg.persistence import atlas

# Diagnostic instrumentation: dump native + Python tracebacks on every
# fatal signal (SIGSEGV, SIGFPE, SIGABRT etc.) and on every thread.
# Enabled at module import so xdist worker subprocesses pick it up at
# their own conftest load.  Without this, "worker crashed" signals
# carry no stack trace, leaving us unable to tell ProactorEventLoop
# IOCP races, native sqlite faults, antivirus-process termination,
# and similar root causes apart.
faulthandler.enable(file=sys.stderr, all_threads=True)

# ── Windows console-flash suppression ──────────────────────────────
#
# On Windows, `subprocess.Popen` (and everything that funnels through
# it -- `subprocess.run`, `asyncio.create_subprocess_exec`, every git
# / python / aws / etc. shell-out across the suite) creates the child
# process with the parent's console attached by default.  When the
# parent is itself a console process (pytest under `python.exe` or
# `uv run python`), the child briefly flashes a console window
# before exiting.  This is purely a UX annoyance during local test
# runs but it stacks up across thousands of tests.
#
# Globally injecting `creationflags=CREATE_NO_WINDOW` at `Popen`
# construction silences every site at once (instead of patching each
# subprocess call individually).  No production code path imports
# this conftest, so the patch is strictly test-only.
if sys.platform == "win32":  # pragma: no cover -- Windows-only branch
    from typing import cast

    _original_popen_init: Any = subprocess.Popen.__init__

    def _no_console_popen_init(self: Any, *args: Any, **kwargs: Any) -> None:
        existing = kwargs.get("creationflags", 0)
        if not isinstance(existing, int):
            existing = 0
        kwargs["creationflags"] = existing | subprocess.CREATE_NO_WINDOW
        _original_popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = cast(Any, _no_console_popen_init)  # type: ignore[method-assign]


class _WriteOnlyDatabase(ExampleDatabase):
    """Wraps a database so it only receives writes -- fetch returns nothing.

    Used for the shared failure log: we want to capture every failing
    example for later analysis, but never replay them automatically
    (that would block all worktrees until someone fixes the bug).
    """

    def __init__(self, db: ExampleDatabase) -> None:
        super().__init__()
        self._db = db

    def save(self, key: bytes, value: bytes) -> None:
        self._db.save(key, value)

    def fetch(self, key: bytes) -> Iterable[bytes]:
        return iter(())

    def delete(self, key: bytes, value: bytes) -> None:
        pass  # No-op: shared DB is a failure log, never delete entries

    def move(
        self,
        src: bytes,
        dest: bytes,
        value: bytes,
    ) -> None:
        self._db.save(dest, value)  # Treat as save-to-dest (preserve the entry)


# ── Hypothesis shared example database ──────────────────────────
# Failing examples are written to a central directory outside any
# worktree so they survive worktree deletion.  The shared DB is
# write-only: failures are logged for analysis but never replayed
# automatically (that would block all test runs until fixed).
# Review captured failures with: ls ~/.synthorg/hypothesis-examples/
#
# The shared dir is namespaced by worktree basename (``Path.cwd``)
# because pre-push hooks across multiple concurrent worktrees on the
# same machine would otherwise contend for the same directory; on
# Windows, two pytest sessions writing to the same hypothesis-examples
# tree can hit ``WinError 32`` sharing-violation races. Per-worktree
# subdirectories isolate the failure log per pre-push run while keeping
# every worktree's history outside its own working tree (so a
# ``git worktree remove`` does not destroy the captured examples).
_local_db = DirectoryBasedExampleDatabase(".hypothesis/examples/")

try:
    _shared_dir = (
        Path.home() / ".synthorg" / "hypothesis-examples" / Path.cwd().resolve().name
    )
    _shared_dir.mkdir(parents=True, exist_ok=True)
    _shared_db: ExampleDatabase = _WriteOnlyDatabase(
        DirectoryBasedExampleDatabase(str(_shared_dir)),
    )
    _local_combined_db = MultiplexedDatabase(_local_db, _shared_db)
except OSError:
    # HOME unwritable (containerized CI, read-only filesystem);
    # fall back to local-only DB.  Failures still captured in
    # .hypothesis/examples/ for the duration of this worktree.
    _local_combined_db = MultiplexedDatabase(_local_db)

settings.register_profile(
    "ci",
    # Deterministic: derandomize=True uses a fixed seed per test function,
    # so the same 10 examples run every time.  Not random, not skipped.
    max_examples=10,
    derandomize=True,
    # ``differing_executors`` warns when the same property test runs
    # from multiple executor instances; pytest-repeat (used by the
    # isolation regression gate, see scripts/run_affected_tests.py)
    # legitimately invokes the same method twice from two separate
    # pytest collection items, so the warning is a false positive in
    # this codebase. Suppression is safe because our property tests do
    # not depend on Hypothesis-database persistence between iterations
    # (derandomize=True pins the seed, and the database is write-only
    # for the shared failure log per ``_WriteOnlyDatabase``).
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.differing_executors,
    ],
)
settings.register_profile(
    "dev",
    max_examples=1000,
    database=_local_combined_db,
)
settings.register_profile(
    "fuzz",
    # Dedicated long-running fuzzing sessions -- run locally or on a
    # schedule.  High example count + no deadline to explore deep
    # input spaces.  Failures captured to shared DB for analysis.
    # Suppress health checks so Hypothesis doesn't abandon slow or
    # heavily-filtered tests before reaching max_examples.
    # IMPORTANT: also pass --timeout=0 to pytest to disable the
    # per-test wall-clock limit (the default 30s kills 10k runs).
    max_examples=10_000,
    deadline=None,
    suppress_health_check=list(HealthCheck),
    database=_local_combined_db,
)
settings.register_profile(
    "extreme",
    # Deep overnight fuzzing -- 500k examples per test, no deadline,
    # no health checks, no seed (true randomness).  Expect hours.
    max_examples=500_000,
    deadline=None,
    suppress_health_check=list(HealthCheck),
    database=_local_combined_db,
)
# Configure Hypothesis globally for the test session.
# Override by setting HYPOTHESIS_PROFILE=dev in the environment.
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))

# ── Vendor-agnostic guardrail ───────────────────────────────────
# Centralized set of disallowed vendor identifiers so tests that
# scan for vendor names do not embed the literals themselves.
DISALLOWED_VENDOR_NAMES: frozenset[str] = frozenset(
    {"anthropic", "openai", "claude", "gpt", "gemini", "mistral"}
)

# ── Slow test guardrail ──────────────────────────────────────────
# Fail any unit test whose *total* wall-clock time (setup + call +
# teardown) exceeds this threshold.  This catches regressions like
# backup-service filesystem I/O in fixtures before they snowball
# into 10-minute test runs.  Integration and e2e tests are exempt.
# Disabled for fuzz profile where 10k examples per test routinely
# exceed the limit.
_UNIT_TEST_WALL_CLOCK_LIMIT = 8.0  # seconds
_FUZZ_PROFILE_ACTIVE = os.environ.get("HYPOTHESIS_PROFILE") in ("fuzz", "extreme")
_start_key = pytest.StashKey[float]()
# Accumulator for unit-only wall-clock time, summed across tests in
# ``pytest_runtest_teardown``.  Used by the suite regression guard
# below to compare per-unit-test cost against the baseline without
# polluting the math with non-unit (integration / e2e / conformance)
# test elapsed time.
#
# Under pytest-xdist (the default ``-n 8`` configuration mandated by
# CLAUDE.md), each worker is its own subprocess with its own copy of
# this module.  Worker-local mutations are NOT visible on the
# controller process where ``pytest_sessionfinish`` runs the
# regression check.  The two hooks below close the gap:
#
#  - ``pytest_sessionfinish`` on each worker copies the worker-local
#    accumulator into ``config.workeroutput`` (which xdist serializes
#    back to the controller).
#  - ``pytest_testnodedown`` on the controller sums the per-worker
#    contributions back into the module-level accumulator, so when
#    the controller's own ``pytest_sessionfinish`` runs the rail it
#    sees the full unit-only elapsed time.
#
# The non-xdist case (single-process pytest) needs neither hook: the
# accumulator is set in teardown and read in sessionfinish in the
# same process.
_unit_elapsed_secs: float = 0.0
_WORKEROUTPUT_KEY = "_unit_elapsed_secs"


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Record wall-clock start time before each test."""
    item.stash[_start_key] = time.monotonic()


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item: pytest.Item) -> None:
    """Fail unit tests that exceed the wall-clock limit + tally per-test elapsed."""
    global _unit_elapsed_secs  # noqa: PLW0603
    start = item.stash.get(_start_key, None)
    if start is None:
        return
    elapsed = time.monotonic() - start
    if item.get_closest_marker("unit"):
        _unit_elapsed_secs += elapsed
    if (
        not _FUZZ_PROFILE_ACTIVE
        and item.get_closest_marker("unit")
        and elapsed > _UNIT_TEST_WALL_CLOCK_LIMIT
    ):
        pytest.fail(
            f"Unit test exceeded {_UNIT_TEST_WALL_CLOCK_LIMIT}s "
            f"wall-clock limit ({elapsed:.1f}s). This usually means "
            f"a fixture is doing heavy I/O -- check setup/teardown.",
            pytrace=False,
        )


# ── Suite-level regression guard ─────────────────────────────────
# Compares total wall-clock time against the committed baseline in
# tests/baselines/unit_timing.json.  Fires after every full suite
# run (locally, in pre-push hooks, in CI).  When a regression is
# detected, prints a loud warning so the cause is investigated
# instead of "fixing" by deleting tests or bypassing hooks.
_BASELINE_PATH = Path(__file__).parent / "baselines" / "unit_timing.json"
_suite_start: float | None = None


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    """Record suite start time + reset the unit-elapsed accumulator."""
    global _suite_start, _unit_elapsed_secs  # noqa: PLW0603
    _suite_start = time.monotonic()
    _unit_elapsed_secs = 0.0


def _load_baseline_for_conftest() -> tuple[float, int, float] | None:
    """Return ``(baseline_secs, baseline_count, threshold_ratio)`` or ``None``.

    Returns ``None`` only when the baseline file does not exist
    (fresh checkout, no baseline yet -- callers skip the regression
    check).  A baseline that exists but is malformed propagates a
    :class:`tests.baselines.loader.BaselineMalformedError`: silently
    disabling the regression rail on a typo would defeat the very
    failure mode it exists to catch.

    Delegates to :func:`tests.baselines.loader.load_baseline_snapshot`
    so the validation contract is identical to the pre-push runner
    (``scripts/run_affected_tests.py``). The 3-tuple shape this
    function returns is what :func:`pytest_sessionfinish` consumes; it
    is rebuilt from the snapshot's ``per_test_ms * baseline_test_count``
    so the snapshot file can carry per-test cost (immune to test-count
    growth) without rippling a shape change through the hook.
    """
    from tests.baselines.loader import load_baseline_snapshot

    snapshot = load_baseline_snapshot(_BASELINE_PATH)
    if snapshot is None:
        return None
    baseline_secs = snapshot.per_test_ms * snapshot.baseline_test_count / 1000.0
    return baseline_secs, snapshot.baseline_test_count, snapshot.threshold_ratio


def _emit_regression_banner(
    *,
    elapsed: float,
    unit_count: int,
    baseline_secs: float,
    baseline_count: int,
    threshold_ratio: float,
) -> None:
    """Print the regression banner to stderr."""
    baseline_per_test_ms = baseline_secs * 1000.0 / baseline_count
    current_per_test_ms = elapsed * 1000.0 / unit_count
    border = "!" * 60
    msg = (
        f"\n{border}\n"
        f"REGRESSION DETECTED: per-test cost "
        f"{current_per_test_ms:.2f}ms exceeds "
        f"{baseline_per_test_ms * threshold_ratio:.2f}ms "
        f"(baseline {baseline_per_test_ms:.2f}ms, "
        f"ratio cap {threshold_ratio:.2f}x).\n"
        f"Suite: {elapsed:.0f}s across {unit_count} tests "
        f"(baseline {baseline_secs:.0f}s across {baseline_count}).\n"
        f"Run A/B against origin/main before fixing anything.\n"
        f"Do NOT delete tests or use --no-verify.\n"
        f"If the new baseline is intentional, update "
        f"tests/baselines/unit_timing.json.\n"
        f"{border}\n"
    )
    print(msg, file=sys.stderr)  # noqa: T201


def pytest_testnodedown(node: pytest.Item, error: object) -> None:
    """xdist controller hook: aggregate per-worker accumulators.

    Each worker writes its local ``_unit_elapsed_secs`` (sum of the
    per-test wall-clock measurements that worker observed) to
    ``workeroutput`` in its own ``pytest_sessionfinish``.  The
    controller folds those values into the module-level accumulator
    here using **MAX**, not SUM:

    - The baseline (``unit_timing.json::unit_suite_seconds``) is
      WALL-CLOCK seconds for the full xdist run -- i.e. how long
      the suite took to complete in real time, with workers running
      in parallel.
    - Summing per-worker per-test elapsed times across N workers
      gives ``N * wall-clock`` (each worker accumulates ~wall-clock
      worth of sequential test execution).  Comparing that against
      a wall-clock baseline would always trip the rail by a factor
      of ~N.
    - The wall-clock duration of the unit suite is approximated by
      the longest-running worker's accumulator (workers all finish
      around the same time when balanced).  ``max`` is dimension-
      consistent with the baseline and stable across worker counts.

    The non-xdist case never reaches this hook -- pytest-xdist only
    invokes it when running with ``-n``.
    """
    global _unit_elapsed_secs  # noqa: PLW0603
    workeroutput = getattr(node, "workeroutput", {})
    if _WORKEROUTPUT_KEY in workeroutput:
        _unit_elapsed_secs = max(
            _unit_elapsed_secs,
            float(workeroutput[_WORKEROUTPUT_KEY]),
        )


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(
    session: pytest.Session,
    exitstatus: int,
) -> None:
    """Fail the suite hard if per-test cost regressed beyond the baseline.

    The metric is per-test milliseconds (``elapsed * 1000 /
    unit_count``) compared against the baseline's per-test cost
    multiplied by ``regression_threshold_ratio`` (default 1.3).
    Mechanical test-count growth (PRs adding tests) does not move this
    metric, so the baseline stays valid until per-test cost actually
    drifts.  See ``tests/baselines/README.md`` for schema.
    """
    # Worker path (pytest-xdist): publish the accumulator to
    # ``workeroutput`` so the controller can sum it in
    # ``pytest_testnodedown``, then return -- workers do not run the
    # regression rail (they only see their own slice of the suite,
    # so per-test math on the worker is meaningless).
    workeroutput = getattr(session.config, "workeroutput", None)
    if workeroutput is not None:
        workeroutput[_WORKEROUTPUT_KEY] = _unit_elapsed_secs
        return
    if _suite_start is None or _FUZZ_PROFILE_ACTIVE:
        return
    if not any(item.get_closest_marker("unit") for item in session.items):
        return
    loaded = _load_baseline_for_conftest()
    if loaded is None:
        return
    baseline_secs, baseline_count, threshold_ratio = loaded
    unit_count = sum(1 for item in session.items if item.get_closest_marker("unit"))
    # Only compare against baseline when the session contains (roughly)
    # the full unit suite.  Mixed CI runs (``pytest tests/`` with
    # ``RUN_INTEGRATION_TESTS=1``) still need regression detection
    # because that's where slowdowns are most damaging -- but the
    # comparison must use unit-only elapsed, not total session
    # wall-clock, otherwise a single slow integration test can trip
    # the rail even when the unit suite is unchanged.
    # ``_unit_elapsed_secs`` is summed across only ``@unit``-marked
    # items in ``pytest_runtest_teardown`` for exactly this reason.
    partial_run = bool(baseline_count) and unit_count < baseline_count * 0.8
    cannot_compute = baseline_count <= 0 or unit_count <= 0
    if partial_run or cannot_compute:
        return
    elapsed = _unit_elapsed_secs
    baseline_per_test_ms = baseline_secs * 1000.0 / baseline_count
    current_per_test_ms = elapsed * 1000.0 / unit_count
    if current_per_test_ms <= baseline_per_test_ms * threshold_ratio:
        return
    _emit_regression_banner(
        elapsed=elapsed,
        unit_count=unit_count,
        baseline_secs=baseline_secs,
        baseline_count=baseline_count,
        threshold_ratio=threshold_ratio,
    )
    # Hard fail: exit status 3 signals test-level failure to CI
    # and pre-push hooks.  This is intentional; regressions
    # beyond the tolerance must block the change, not just warn.
    # Never overwrite an already-failing exit status -- doing so
    # would mask the primary failure mode (test failures, collection
    # errors, fixture errors) and make CI diagnostics noisier.
    if session.exitstatus == 0:
        session.exitstatus = 3


def clear_logging_state() -> None:
    """Clear structlog context and stdlib root handlers.

    Shared helper for observability test fixtures that need to reset
    logging state between tests.
    """
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.WARNING)


# ── Module-global cache resets (xdist isolation) ──────────────────
# A handful of subsystems hold a process-global cache by design (the
# Agent Card cache, the Prometheus label-validator snapshot). Without
# a global autouse reset, a test in worker N that imports the
# subsystem leaves entries behind for the next test in the same
# worker -- the canonical "module-level state survives across tests
# in one xdist worker" failure mode that turns a green local run
# into a flake under ``-n 8``. Each fixture is O(1) (a dict
# ``.clear()`` or a single rebind) so the suite-wide cost is
# negligible.


@pytest.fixture(autouse=True)
def _reset_structlog_state() -> Iterator[None]:
    """Reset structlog defaults between every test.

    structlog's defaults (processors, wrapper class, context-vars
    binding) are process-level, so a test that calls
    ``structlog.configure(...)`` or holds ``structlog.testing.capture
    _logs()`` open across an unexpected exit leaves residual state for
    the next test in the same xdist worker. Without this autouse the
    canonical symptom is a settings-resolution test under ``-n 8``
    finding only DEBUG events in the capture buffer because a prior
    test left structlog wired to a filter that swallows INFO
    emissions, even though the production code emitted them.

    Scoped to ``structlog`` defaults + stdlib root *level* only --
    the stdlib-root-handler close that ``clear_logging_state()``
    performs is intentionally NOT applied globally because tests that
    import ``synthorg.observability`` at module load time hold
    long-lived handler references the global reset would close out
    from under them. Observability tests retain their dedicated
    ``_reset_logging`` autouse for that broader reset.

    Also resets ``logging.root.level`` to NOTSET because production
    boot code (``setup_logging``) may have left it at WARNING during a
    prior test, which silently filters out INFO events that production
    code emits via ``structlog.stdlib.BoundLogger`` -- the canonical
    symptom for ``test_source_resolution_log.py`` and
    ``test_service.py::test_emits_audit_event`` failing under -n 8.
    """
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    logging.getLogger().setLevel(logging.NOTSET)
    yield
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    logging.getLogger().setLevel(logging.NOTSET)


@pytest.fixture(autouse=True)
def _reset_a2a_card_cache() -> Iterator[None]:
    """Clear ``synthorg.a2a.well_known._card_cache`` before and after every test.

    The cache is intentionally module-global at runtime (Agent Cards
    are expensive to rebuild and the controller serves them under TTL
    from a single dict). For tests, every test must start with an
    empty cache so a stale entry from a prior test cannot satisfy a
    later host-key probe; the post-test clear protects the next
    worker hop in xdist work-stealing.
    """
    from synthorg.a2a.well_known import _card_cache

    _card_cache.clear()
    yield
    _card_cache.clear()


@pytest.fixture(autouse=True)
def _reset_prometheus_label_snapshot() -> Iterator[None]:
    """Reset ``prometheus_labels._snapshot`` before every test.

    The snapshot is seeded by ``PrometheusCollector.refresh()`` and
    consulted by every metric ``record_*`` call to validate label
    cardinality. Without this reset, a refresh in observability tests
    leaves the snapshot non-empty for unrelated engine / api tests
    that import the metrics path, surfacing as spurious
    ``validate_*`` passes that should have failed closed during
    bootstrap.
    """
    from synthorg.observability.prometheus_labels import (
        _reset_label_snapshot_for_tests,
    )

    _reset_label_snapshot_for_tests()
    yield
    _reset_label_snapshot_for_tests()


_TEMPLATE_DB: Path | None = None
"""Session-wide migrated template DB.  Created once, copied per test."""


async def _get_template_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return path to the session-wide migrated template database.

    Migrates a fresh SQLite file via Atlas on first call, then
    reuses the same file for all subsequent calls.  Each test
    copies this file instead of spawning a new Atlas subprocess.
    """
    global _TEMPLATE_DB  # noqa: PLW0603
    if _TEMPLATE_DB is not None:
        return _TEMPLATE_DB
    base = tmp_path_factory.mktemp("atlas_template")
    db_path = base / "template.db"
    rev_url = atlas.copy_revisions(base / "revisions")
    await atlas.migrate_apply(
        atlas.to_sqlite_url(str(db_path)),
        revisions_url=rev_url,
        skip_lock=True,
    )
    _TEMPLATE_DB = db_path
    return _TEMPLATE_DB


@pytest.fixture
def mock_dispatcher() -> Any:
    """``AsyncMock`` conforming to the full ``NotificationDispatcher`` contract.

    Spec covers ``register`` / ``start`` / ``aclose`` / ``dispatch`` so a
    test cannot accidentally call a method the production class does not
    expose. Use this fixture in any test that needs to stand in for
    ``NotificationDispatcher`` -- it replaces the previous
    ``MagicMock(spec=NotificationDispatcher); dispatcher.dispatch =
    AsyncMock()`` pattern that defeated the spec by overwriting one
    method with a bare ``AsyncMock()``.
    """
    from unittest.mock import AsyncMock

    from synthorg.notifications.dispatcher import (
        NotificationDispatcher,
    )

    return AsyncMock(spec=NotificationDispatcher)


@pytest.fixture
async def migrated_db(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncGenerator[aiosqlite.Connection]:
    """Temp-file SQLite connection with Atlas migrations applied.

    Copies a session-wide template database instead of spawning
    an Atlas subprocess per test -- eliminates ~hundreds of Go
    process launches during a full test run.
    """
    template = await _get_template_db(tmp_path_factory)
    db_path = tmp_path / "test.db"
    shutil.copy2(str(template), str(db_path))
    db = await aiosqlite.connect(str(db_path))
    try:
        db.row_factory = aiosqlite.Row
        yield db
    finally:
        await db.close()
