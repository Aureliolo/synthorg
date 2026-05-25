"""Root test configuration and shared fixtures.

Cross-worker coordination rule (read before adding a new fixture):

    Any setup that uses a cross-worker primitive -- ``filelock.FileLock``
    over a path under ``tmp_path_factory.getbasetemp().parent``, a
    testcontainers / Docker container shared across workers, or any
    other off-process lock -- MUST run in a ``pytest_sessionstart``
    hook, NOT in a fixture (not even ``scope="session", autouse=True``).

    Why not autouse session fixtures: pytest resolves session-scope
    fixtures (including autouse ones) during the FIRST referencing
    test's ``pytest_runtest_setup`` phase, which IS covered by
    ``pytest-timeout``. Autouse just MARKS every test as a referencer;
    it does not move setup outside the per-test phase. An autouse
    session fixture wrapping a slow cross-worker template build will
    therefore kill workers at +30s on whichever test happens to be
    the first one dispatched to that worker, not on the build itself.

    Why pytest_sessionstart works: the hook runs in ``pytest_collection``
    BEFORE any test, is NOT covered by ``pytest-timeout``, and runs
    once per xdist worker subprocess. The lock wait + container start
    + readiness polling all happen there; by the time any test starts,
    the cached state is ready and the fixture is a trivial cache read.

    Symptoms when this rule is broken: workers die at exactly
    ``last-passed + 30.0x s`` with ``[gwN] node down: Not properly
    terminated``, no banner reaches the master through xdist IPC
    because pytest-timeout's ``os._exit(1)`` outruns the Channel
    flush, and the dying tests look random across runs. Core dump
    (after we patched pytest-timeout to ``os.abort()``) showed the
    main thread in ``selectors.select`` and a background thread in
    ``filelock/_api.py:517 in acquire``: the per-test timer was
    counting the cross-worker lock wait.

    Existing instances of the pattern, all following this rule:

    * ``pytest_sessionstart`` (this file): drains the FileLock-
      coordinated yoyo migration template build via
      ``_get_template_db`` before any per-test timer starts.
    * ``pytest_sessionstart`` in
      ``tests/conformance/persistence/conftest.py``: drains the
      FileLock-coordinated testcontainer start, caches the result
      in a module-level state dict; the ``postgres_container``
      fixture reads from that cache without any lock work.

    If you add a new cross-worker coordination point, follow the
    same shape and link to this rule in its hook docstring.
"""

import asyncio
import faulthandler
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import AsyncGenerator, Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, override

if TYPE_CHECKING:
    from unittest.mock import AsyncMock

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

from synthorg.persistence import migrations

# ``socket.getfqdn()`` does a reverse-DNS lookup that on GHA Linux runners
# without configured reverse-DNS can block for 10-30+ seconds on the first
# call per worker. yoyo's ``log_migration`` calls it (no argument) to
# stamp the migration log table with the host name. When the cross-worker
# template-build FileLock in ``_get_template_db`` serialises 4-8 xdist
# workers behind one slow build, the workers waiting on the lock have
# that wait counted against the per-test 30s ``pytest-timeout``: the
# first ``migrated_db`` user on each waiting worker dies at exactly
# t+30s (verified in CI: three workers, three deltas all 30.07s). The
# wrapper below short-circuits the no-argument call (the only path yoyo
# uses) to the local ``gethostname`` (no DNS), and falls back to the
# real ``getfqdn`` when a specific hostname is passed -- so any future
# caller that wants the real reverse-DNS resolution of an arbitrary
# host still gets it. Patched after the import block (instead of in
# line with it) because yoyo resolves ``socket.getfqdn`` at CALL time,
# not at module import, so the patch takes effect as long as it runs
# before any migration apply.
_orig_getfqdn = socket.getfqdn


def _fast_getfqdn(name: str = "") -> str:
    """Short-circuit the no-arg path that yoyo's migration logger uses.

    ``socket.getfqdn()`` (no argument) on Linux CI without reverse-DNS
    can block 10-30s. ``socket.gethostname()`` returns the same value
    yoyo cares about for the migration log table without any DNS work.
    Pass-through for the rare ``socket.getfqdn(host)`` form so we don't
    silently break a caller that actually wants the real resolution.
    """
    if not name:
        return socket.gethostname()
    return _orig_getfqdn(name)


socket.getfqdn = _fast_getfqdn


# ── pytest-timeout: guarantee a visible stack on every fire ─────────
#
# pytest-timeout in ``thread`` mode (configured in pyproject) writes its
# pre-kill banner + thread stacks via ``terminal.write(...)`` and then
# calls ``os._exit(1)`` (see pytest_timeout.py:534-542). That terminal
# write goes through pytest's TerminalWriter -> xdist Channel ->
# execnet IPC, all of which buffer. ``os._exit`` 5 lines later kills
# the worker process before the IPC buffer has drained, so the banner
# never reaches the master's log. Empirically: xdist workers die at
# exactly +30.06s after their last passed test (the per-test timeout)
# with ZERO ``+++ Timeout +++`` banners in the captured log.
#
# ``faulthandler.dump_traceback`` writes raw bytes to the stderr fd via
# ``os.write``, bypassing all Python and xdist buffering. execnet
# captures the worker's stderr at the pipe level, so the dump always
# reaches the master's log before the worker process exits.
#
# Our patched ``pytest_timeout.timeout_timer`` (1) dumps every thread's
# Python stack via ``faulthandler.dump_traceback(all_threads=True)``,
# and then (2) calls ``os.abort()`` to force SIGABRT. It does NOT
# delegate to the original ``timeout_timer`` -- abort takes the process
# down directly, skipping the original's ``os._exit(1)`` path entirely.
# The reason for ``abort`` over ``os._exit``: SIGABRT (under
# ``ulimit -c unlimited``, already set by the CI workflow's "Enable
# core dumps" step) writes a core file at the runner's
# ``kernel.core_pattern`` path, which the "Upload core dumps" step
# then surfaces as a build artefact. That core lets pystack/gdb
# resolve the C-level frames for threads blocked in sqlite3 /
# aiosqlite executor / etc. that faulthandler shows by name only.
#
# Why this is safe (vs the ``dump_traceback_later(repeat=True)`` we
# removed in round 14): this dump runs from Python code holding the
# GIL, not from faulthandler's dedicated C timer thread without the
# GIL. Holding the GIL means no other thread can be midway through
# ``PyThreadState_Delete`` while we walk ``interp->threads.head``;
# the chain-walk race that crashed CPython in round 13 cannot fire
# from here.
try:
    import pytest_timeout as _pytest_timeout  # type: ignore[import-untyped]

    def _timeout_timer_with_faulthandler(item: Any, settings: Any) -> None:
        # 1) Dump Python frames for every thread via faulthandler (raw
        #    fd write, bypasses pytest/xdist IPC -> always reaches log).
        sys.stderr.write(
            "\n==== pytest-timeout fired: faulthandler all-threads dump"
            " (raw stderr write, bypasses pytest/xdist IPC) ====\n"
        )
        sys.stderr.flush()
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        sys.stderr.write("==== end faulthandler dump ====\n")
        sys.stderr.flush()
        # 2) ``os.abort()`` instead of pytest-timeout's stock
        #    ``os._exit(1)``: abort sends SIGABRT, which (with the
        #    ``ulimit -c unlimited`` already set in the CI workflow)
        #    generates a core file at ``core.%e.%p.%t``. The "Upload
        #    core dumps" step then surfaces it as a build artefact we
        #    can ``pystack core <core> <python-bin>`` to recover the
        #    C-level frames that faulthandler cannot see (threads
        #    blocked in sqlite3 / aiosqlite executor / etc. show their
        #    name but no Python frame in the faulthandler dump above).
        sys.stderr.write(
            "==== forcing SIGABRT for core dump (gdb/pystack reveals"
            " the C-level stack faulthandler cannot show) ====\n"
        )
        sys.stderr.flush()
        os.abort()

    _pytest_timeout.timeout_timer = _timeout_timer_with_faulthandler
except ImportError:
    # pytest-timeout not installed (e.g. minimal environment); the
    # guard above keeps the broader conftest functional.
    pass

# Diagnostic instrumentation: dump native + Python tracebacks on every
# fatal signal (SIGSEGV, SIGFPE, SIGABRT etc.) and on every thread.
# Enabled at module import so xdist worker subprocesses pick it up at
# their own conftest load. ``faulthandler.enable`` installs signal
# handlers only -- it never runs unless a real signal arrives, so it
# is safe.
#
# ``faulthandler.dump_traceback_later(..., repeat=True)`` (a periodic
# watchdog timer) is INTENTIONALLY NOT installed here. The timer runs
# in its own dedicated C thread that walks the interpreter's
# ``PyThreadState`` chain WITHOUT holding the GIL (the whole point of
# the timer is to work even when the GIL is wedged). On a busy test
# worker, threads are created and destroyed continuously -- aiosqlite
# spawns a per-connection executor thread, ``logging.handlers``
# QueueListener threads come and go per worker, every async test
# briefly spawns its own loop thread. Between
# ``_Py_DumpTracebackThreads`` reading ``interp->threads.head`` and
# walking the ``tstate->next`` chain, another thread can complete
# ``PyThreadState_Delete`` and free the next tstate; the timer reads
# a dangling pointer and segfaults on ``tstate_is_freed(tstate=<small
# garbage>)`` inside ``Python/traceback.c``. We confirmed this with
# pystack + gdb on a CI core dump: SEGV at NULL+0x211, frame
# ``tstate_is_freed`` -> ``_Py_DumpTracebackThreads`` ->
# ``faulthandler_thread`` (see CPython issue 103619 family for the
# upstream-known race class). Crashes were random across SQLite-touching
# tests, matching the statistical signature of a teardown race.
#
# Per-test hang detection still works: ``timeout_method = "thread"``
# in pyproject.toml plus the 30s ``timeout`` marker fire a real
# ``KeyboardInterrupt`` into the worker on a hung test, which xdist
# turns into a named test failure (``--max-worker-restart=0`` keeps
# crashed workers reported, not silently restarted). What we lose is
# the periodic all-thread stack dump while a hang is live; that
# trade-off is mandatory because the watchdog itself was the cause of
# the worker SEGVs we were trying to diagnose.
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


# ── pytest-xdist loadscope crash-during-collection guard ───────────
#
# On Windows + Python 3.14, a worker subprocess intermittently dies
# while it is still COLLECTING (importing test modules) -- before it
# registers its collection with the controller. When any *other* worker
# then finishes collecting, pytest-xdist 3.8.0's loadscope scheduler
# (the base of ``--dist=loadfile``) runs ``schedule()``, which calls
# ``_reschedule(node)`` for **every** node in ``self.nodes`` -- including
# the vanished one. ``_assign_work_unit`` then does
# ``self.registered_collections[node]`` and raises ``KeyError`` on the
# dead node, surfacing as an ``INTERNALERROR`` that aborts the ENTIRE
# run and discards every already-collected result.
#
# Guarding ``_reschedule`` to skip a node with no registered collection
# is correct and lossless: such a node cannot be assigned work (there is
# no collection to index into), and skipping it leaves that work in
# ``self.workqueue`` for live nodes to pick up while xdist's normal
# crash handling redistributes anything already assigned. The
# catastrophic whole-run abort becomes graceful redistribution.
#
# Scoped to the controller process (where the scheduler lives) and
# idempotent. Revisit when the pinned pytest-xdist is bumped past a
# release that fixes the upstream reschedule-after-crash KeyError.
def _install_xdist_loadscope_crash_guard() -> None:
    try:
        from xdist.scheduler.loadscope import LoadScopeScheduling
    except ImportError:
        return
    original = LoadScopeScheduling._reschedule
    if getattr(original, "_synthorg_crash_guarded", False):
        return

    def _guarded_reschedule(self: Any, node: Any) -> Any:
        if node not in self.registered_collections:
            return None
        return original(self, node)

    _guarded_reschedule._synthorg_crash_guarded = True  # type: ignore[attr-defined]
    LoadScopeScheduling._reschedule = _guarded_reschedule


_install_xdist_loadscope_crash_guard()


class _WriteOnlyDatabase(ExampleDatabase):
    """Wraps a database so it only receives writes -- fetch returns nothing.

    Used for the shared failure log: we want to capture every failing
    example for later analysis, but never replay them automatically
    (that would block all worktrees until someone fixes the bug).
    """

    def __init__(self, db: ExampleDatabase) -> None:
        super().__init__()
        self._db = db

    @override
    def save(self, key: bytes, value: bytes) -> None:
        self._db.save(key, value)

    @override
    def fetch(self, key: bytes) -> Iterable[bytes]:
        return iter(())

    @override
    def delete(self, key: bytes, value: bytes) -> None:
        pass  # No-op: shared DB is a failure log, never delete entries

    @override
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
#
# The one-time per-worker migration-template build is credited via
# ``_template_build_secs`` and subtracted from the guard comparison
# (see ``pytest_runtest_teardown``), so this budget is for the test
# work itself, not setup spillover. The worst recorded unit test on
# this repo is 5.75s; a 6s cap leaves headroom for xdist-contention
# spikes while firing the moment a unit test starts doing genuine
# integration work (real subprocess, real network, real heavy I/O)
# that belongs in ``tests/integration/`` instead.
_UNIT_TEST_WALL_CLOCK_LIMIT = 6.0  # seconds
_FUZZ_PROFILE_ACTIVE = os.environ.get("HYPOTHESIS_PROFILE") in ("fuzz", "extreme")
# pytest-repeat's ``--count`` flag is used exclusively by
# ``scripts/run_affected_tests.py``'s isolation regression gate (a
# ``--count 2`` replay of every affected test to detect fixture
# state leaks). That replay doubles xdist contention; legitimate
# unit tests routinely cross the 6s wall-clock guard on Windows
# under that load even though they run well under it in the primary
# pass. The primary run (no ``--count``) still enforces the guard;
# disabling it on the replay keeps the gate focused on its actual
# purpose (fixture leak detection) rather than re-litigating timing.
_COUNT_ISOLATION_RUN = "--count" in sys.argv or any(
    arg.startswith("--count=") for arg in sys.argv
)
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

# Wall-clock seconds spent generating the session-wide migration template
# inside a test's fixture setup. The unlucky test on the worker that wins
# the cross-worker lock pays the full ``migrate_apply`` cost; that one-time,
# session-amortised cost must not count against its per-test wall-clock
# budget, otherwise a growing yoyo migration chain trips the guard on an
# arbitrary persistence test. Recorded by ``_get_template_db``, subtracted
# from the guard comparison in ``pytest_runtest_teardown``, reset once
# consumed. Worker-local (each xdist worker imports its own conftest), which
# is correct because the build and its triggering test share a worker.
_template_build_secs: float = 0.0


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Record wall-clock start time before each test."""
    item.stash[_start_key] = time.monotonic()


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item: pytest.Item) -> None:
    """Fail unit tests that exceed the wall-clock limit + tally per-test elapsed."""
    global _unit_elapsed_secs, _template_build_secs  # noqa: PLW0603
    start = item.stash.get(_start_key, None)
    if start is None:
        return
    elapsed = time.monotonic() - start
    if item.get_closest_marker("unit"):
        _unit_elapsed_secs += elapsed
    # The one-time session-template migration build (amortised across the
    # whole suite) lands in whichever test's fixture setup wins the
    # cross-worker lock. Exclude it from the per-test guard comparison so a
    # growing migration chain never trips the limit on an arbitrary
    # persistence test; the suite-timing accumulator above keeps the full
    # elapsed so total-runtime accounting is unaffected.
    guard_elapsed = elapsed
    if _template_build_secs > 0.0:
        guard_elapsed = max(0.0, elapsed - _template_build_secs)
        _template_build_secs = 0.0
    if (
        not _FUZZ_PROFILE_ACTIVE
        and not _COUNT_ISOLATION_RUN
        and item.get_closest_marker("unit")
        and guard_elapsed > _UNIT_TEST_WALL_CLOCK_LIMIT
    ):
        pytest.fail(
            f"Unit test exceeded {_UNIT_TEST_WALL_CLOCK_LIMIT}s "
            f"wall-clock limit ({guard_elapsed:.1f}s). This usually means "
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


def _session_needs_postgres(session: pytest.Session) -> bool:
    """Return True iff this session is going to run any postgres-backed test.

    The conformance/integration/e2e arms use a real postgres -- either
    via ``SYNTHORG_TEST_POSTGRES_*`` env vars (CI service-container) or
    via a per-session testcontainer. Unit shards never touch postgres,
    so we MUST NOT pre-acquire the testcontainer there: an unconditional
    pre-acquire runs the postgres FileLock + container start on every
    shard, and unit-shard non-leader workers will time out at 180s
    waiting on the FileLock. Worker deaths in ``pytest_sessionstart``
    leave the workflow step exit-0 (xdist does not fail when workers
    die before any test runs), so the unit shard reports tests=0 with
    no clear failure signal.

    Detection (any single match is enough):

    1. ``SYNTHORG_TEST_POSTGRES_HOST`` env var set -- CI integration
       shard signal, the only authoritative one.
    2. Marker expression includes ``integration`` or ``e2e`` -- local
       dev ``-m integration`` or the CI integration / e2e jobs.
    3. Any session arg path contains ``conformance``, ``integration``,
       or ``e2e`` -- local dev ``pytest tests/conformance/...`` or
       ``pytest tests/integration/...`` without an explicit marker.
    """
    if os.environ.get("SYNTHORG_TEST_POSTGRES_HOST"):
        return True
    # ``-k "not postgres"`` is an explicit "deselect all postgres
    # parametrisations" signal -- the conformance-sqlite CI job uses
    # exactly this to run only the sqlite arm of the dual-backend
    # ``backend`` fixture. Pre-acquiring a postgres testcontainer
    # there is wasted work AND introduces FileLock contention that
    # blocks the migrated_db builder for a multi-process xdist run.
    keywordexpr = str(getattr(session.config.option, "keyword", "") or "").lower()
    if "not postgres" in keywordexpr:
        return False
    markexpr = str(getattr(session.config.option, "markexpr", "") or "").lower()
    if "integration" in markexpr or "e2e" in markexpr:
        return True
    args = [str(a).lower() for a in (session.config.args or [])]
    needles = ("conformance", "integration", "e2e")
    return any(needle in arg for arg in args for needle in needles)


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    """Record suite start time + pre-build the migrated_db template.

    Two unrelated things happen here, both required to run BEFORE the
    first test's per-test timer starts:

    1. Reset the suite-timing accumulator.

    2. Pre-build the migrated_db template via the cross-worker
       FileLock in :func:`_get_template_db`. The lock is session-level
       coordination, not per-test work, and the wait MUST NOT count
       against ``pytest-timeout``'s 30s per-test budget.

       A ``@pytest.fixture(scope="session", autouse=True)`` does NOT
       move the work out of the per-test phase: pytest resolves
       session-scope fixtures during the FIRST referencing test's
       ``pytest_runtest_setup``, which IS covered by ``pytest-timeout``.
       Empirically, that pattern kills workers at +30s on whichever
       unrelated test happens to be the first one dispatched to each
       worker, with faulthandler showing only the wrapper frame while
       every other thread is blocked in C code waiting on the FileLock.

       ``pytest_sessionstart`` runs before any test, is NOT covered by
       ``pytest-timeout``, and runs once per xdist worker subprocess.
       Doing the FileLock-coordinated build here is the correct
       architectural fix.
    """
    # Wrap the entire body in a forensic try/except. If anything raises
    # in pytest_sessionstart on an xdist worker, pluggy propagates the
    # exception through the IPC channel -- but the worker often dies
    # before the master receives the formatted traceback (we have
    # observed `[gwN] node down: Not properly terminated` with no
    # banner). Writing the traceback to the raw stderr fd via
    # ``faulthandler.dump_traceback`` bypasses TerminalWriter/xdist
    # buffering, and ``os.abort()`` produces SIGABRT so a core dump
    # lands at ``/proc/sys/kernel/core_pattern`` (set to the workspace
    # in the CI "Enable core dumps" step). Without this, a death here
    # is silent: no banner, no core, no diagnosis path.
    import traceback as _traceback

    try:
        global _suite_start, _unit_elapsed_secs  # noqa: PLW0603
        _suite_start = time.monotonic()
        _unit_elapsed_secs = 0.0
        # ``session.config._tmp_path_factory`` is the underlying
        # _pytest.tmpdir.TempPathFactory pytest uses to back the
        # ``tmp_path_factory`` fixture. At sessionstart no fixtures
        # are resolved yet, so we read the private attribute
        # (canonical workaround used inside pytest's own test suite).
        tmp_path_factory = session.config._tmp_path_factory  # type: ignore[attr-defined]
        asyncio.run(_get_template_db(tmp_path_factory))

        # The conformance/persistence conftest's ``pytest_sessionstart``
        # does NOT fire on xdist workers because that conftest is
        # loaded lazily during collection (after sessionstart). The
        # root conftest IS loaded eagerly (we are it), so we call the
        # conformance helper from here, but ONLY for sessions that
        # actually run postgres-backed tests. Unit shards do not need
        # a postgres testcontainer; an unconditional call holds the
        # FileLock long enough for non-leader workers to time out at
        # 180s, leaving unit shards with tests=0 and worker crashes.
        if _session_needs_postgres(session):
            from tests.conformance.persistence.conftest import (
                _pre_acquire_postgres_container_state,
            )

            _pre_acquire_postgres_container_state(session)
    except BaseException as exc:
        worker = os.environ.get("PYTEST_XDIST_WORKER", "master")
        sys.stderr.write(
            f"\n==== pytest_sessionstart FAILED on worker={worker} ====\n"
            f"Exception: {type(exc).__name__}: {exc}\n"
        )
        sys.stderr.flush()
        _traceback.print_exc(file=sys.stderr)
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        sys.stderr.write("==== aborting for core dump ====\n")
        sys.stderr.flush()
        os.abort()


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
"""Worker-local cache of the session-wide migrated template DB path."""

# Catastrophe ceiling, not the expected wait. Followers (non-builders)
# spend their actual wait in the poll-loop below: ~5s after the leader
# finishes, regardless of how long the leader's build took. 600s only
# fires if the leader genuinely hangs (deadlock, segv before fcntl
# release, etc.) and ``filelock``'s OS-level auto-release on death
# fails to fire. The conformance/persistence postgres coordinator
# carries the same 600s ceiling for the same reason.
_FILE_LOCK_TIMEOUT_SECONDS: Final[int] = 600

# Poll-slice for follower acquire attempts. Short enough that a
# follower exits within one slice of the leader finishing; long enough
# that we don't thrash on the lockfile under heavy contention.
_FILE_LOCK_POLL_SLICE_SECONDS: Final[float] = 5.0


async def _get_template_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return path to the session-wide migrated template database.

    Migrates a fresh SQLite file via yoyo ONCE per pytest session,
    shared across all pytest-xdist workers via a ``FileLock`` on a
    directory under ``tmp_path_factory.getbasetemp().parent`` (the
    directory xdist allocates for the whole run -- shared across
    workers, unlike ``mktemp`` which is worker-local). Without the
    cross-worker lock every worker would re-run yoyo migrations on
    its first persistence test (8x cost under ``-n 8``), and the
    per-test wall-clock guard would fire on the first persistence
    test in each worker once the migration chain grows past ~7 steps.

    Followers use a poll-acquire loop (short slices + db-existence
    re-check between slices) instead of a single
    ``lock.acquire(timeout=_FILE_LOCK_TIMEOUT_SECONDS)`` so the
    follower wait tracks the leader's ACTUAL build time, not the
    catastrophe ceiling. Cold-cache leader builds can exceed the
    historical 180s budget on slow CI runners under matrix sharding,
    so the poll-loop keeps the budget elastic upward without making
    the worst-case-success wait any longer than necessary. Pattern
    mirrors ``tests/conformance/persistence/conftest.py``'s Postgres
    testcontainer coordination; that path uses a raw timeout because
    its refcount semantics require an acquire on every worker (so
    polling does not help), but the ceiling is aligned with this one.
    """
    global _TEMPLATE_DB  # noqa: PLW0603
    if _TEMPLATE_DB is not None and await asyncio.to_thread(_TEMPLATE_DB.exists):
        return _TEMPLATE_DB
    # ``getbasetemp().parent`` is the xdist run-wide base; ``getbasetemp()``
    # itself is the worker-local ``popen-gw0`` etc. subdir.
    shared_dir = tmp_path_factory.getbasetemp().parent / "yoyo_template_shared"
    await asyncio.to_thread(shared_dir.mkdir, parents=True, exist_ok=True)
    db_path = shared_dir / "template.db"
    building_path = shared_dir / "template.db.building"
    lock_path = shared_dir / "template.lock"
    from filelock import FileLock, Timeout

    # Fast path: template already built by another process. The
    # builder below writes to ``template.db.building`` and atomically
    # renames to ``template.db``, so a present ``db_path`` is always
    # the complete migrated file -- followers can read it without
    # holding the lock and without racing a partial write.
    if await asyncio.to_thread(db_path.exists):
        _TEMPLATE_DB = db_path
        return _TEMPLATE_DB

    # Poll-acquire: try the lock in short slices, re-checking
    # ``db_path.exists()`` between slices. A follower exits the loop
    # via the existence check (leader finished) OR via a successful
    # acquire (this worker is the new leader). Total wall-clock is
    # bounded by ``_FILE_LOCK_TIMEOUT_SECONDS`` so a wedged lock
    # eventually raises rather than blocking forever.
    def _try_acquire(slice_s: float) -> FileLock | None:
        lock = FileLock(str(lock_path), timeout=slice_s)
        try:
            lock.acquire()
        except Timeout:
            return None
        return lock

    deadline = time.monotonic() + _FILE_LOCK_TIMEOUT_SECONDS
    lock: FileLock | None = None
    while True:
        if await asyncio.to_thread(db_path.exists):
            _TEMPLATE_DB = db_path
            return _TEMPLATE_DB
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            msg = (
                f"Timed out after {_FILE_LOCK_TIMEOUT_SECONDS}s waiting "
                f"for the cross-worker template DB build at {lock_path}. "
                f"Either the leader's yoyo migration chain exceeded the "
                f"catastrophe ceiling or the lock is wedged."
            )
            raise TimeoutError(msg)
        slice_s = min(_FILE_LOCK_POLL_SLICE_SECONDS, remaining)
        lock = await asyncio.to_thread(_try_acquire, slice_s)
        if lock is not None:
            break

    global _template_build_secs  # noqa: PLW0603
    try:
        # Re-check existence under the lock: another worker may have
        # built it between our poll-loop existence check above and our
        # acquire below.
        if not await asyncio.to_thread(db_path.exists):
            build_start = time.monotonic()
            rev_path = migrations.copy_revisions(shared_dir / "revisions")
            # Build to a sibling path then atomically rename. SQLite
            # creates the .db file at first open, so a direct migration
            # to ``db_path`` would make a partial file visible to the
            # fast-path existence check above; rename makes the file
            # appear atomically only when the build is complete.
            await asyncio.to_thread(building_path.unlink, missing_ok=True)
            await migrations.migrate_apply(
                migrations.to_sqlite_url(str(building_path)),
                revisions_path=rev_path,
            )
            await asyncio.to_thread(building_path.replace, db_path)
            # Credit the one-time build so the triggering test's per-test
            # wall-clock guard does not count it (see _template_build_secs).
            _template_build_secs += time.monotonic() - build_start
    finally:
        await asyncio.to_thread(lock.release)
    _TEMPLATE_DB = db_path
    return _TEMPLATE_DB


@pytest.fixture
def mock_dispatcher() -> AsyncMock:
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
    """Temp-file SQLite connection with yoyo migrations applied.

    Copies a session-wide template database instead of re-running
    migrations per test -- amortises the per-revision work across
    the suite. The shared template is built by
    :func:`_prebuild_migrated_db_template` at session start, so by
    the time any test calls this fixture the template already exists
    and ``_get_template_db`` short-circuits to the cached path.
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
