#!/usr/bin/env python3
"""Pre-push hook: run only unit tests affected by changed files.

Uses git diff against origin/main to determine which source modules changed,
then maps them to their corresponding test directories via the project's 1:1
``src/synthorg/<module>/`` -> ``tests/unit/<module>/`` layout.
Only Python (``.py``) file changes are considered; non-Python changes are ignored.

Foundational modules (core, config, observability) are imported by nearly every
other module, so a change there raises a whole-suite question. Answering it is
CI's job (the Test Unit shards): locally the changed module's own tests still
run and the deferral is printed, never silent, so a push stays inside its
five-minute budget. The same applies to a ``conftest.py``, shared test
infrastructure under ``tests/`` that belongs to no tier, a top-level source
file (``__init__.py``, ``constants.py``), and a ``pyproject.toml`` edit, which
carries pytest's own configuration. ``--full`` runs the whole suite on demand,
with the timing-regression guards armed.

A changed test file usually selects only itself: almost nothing imports a test
module, so its siblings verify nothing the change could have broken. The
exception is the handful that other test modules DO import for a shared fake or
helper, and for those the importers' packages are selected as well -- otherwise
breaking a shared fake would pass a push having run only the file that defines
it. Scoping by package instead would cost the most exactly where there is no
source package to scope against: ``tests/unit/scripts`` is a thirteenth of the
unit tier by collected cases, and every gate's tests share it.

A change can also be too broad to fit the budget without any of those triggers
firing, simply by touching many packages at once. Past
``_MAX_AFFECTED_TEST_FILES`` the unit run is deferred whole for the same reason
and with the same announcement: at that breadth the local run has stopped being
a fast screen and become a slower copy of what CI is about to do anyway.

The affected-tests run uses ``--max-worker-restart=0`` (matching CI) so any
xdist worker crash (commonly the Python 3.14 + Windows ProactorEventLoop
IOCP teardown race) surfaces and blocks the push rather than being silently
recovered: a crashed worker is a real defect to debug from the
faulthandler/core dump, not flakiness to wave through.

Exit codes match pytest: 0 (passed/nothing to run), 1 (failures),
etc.  Git command failures fall back to running the full unit suite.
"""

import argparse
import contextlib
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Final, Literal

# Hard wall-clock caps so a Windows + Python 3.14 + xdist IOCP teardown
# hang in pytest cannot indefinitely block a push. Empirical baseline is
# ~3 min for the full unit suite; a 12 min cap leaves 4x headroom while
# still failing fast when the suite genuinely wedges. Affected-only runs
# rarely exceed 2 min, so 6 min keeps the same 3x headroom shape.
_PYTEST_FULL_SUITE_TIMEOUT_SECONDS: Final[float] = 12 * 60
_PYTEST_AFFECTED_TIMEOUT_SECONDS: Final[float] = 6 * 60
_PYTEST_HUNG_EXIT_CODE: Final[int] = 124  # matches GNU coreutils ``timeout(1)``
# ``taskkill /T`` walks the tree and returns; anything slower than this means
# the kill itself is stuck, and waiting longer on the killer of a hung run
# only compounds the hang it was called to end.
_TASKKILL_TIMEOUT_SECONDS: Final[float] = 5.0

# Written by ``warm_typeguard_cache.py --mark-failures``. Duplicated as a
# literal rather than imported: that module imports typeguard at module
# level, and pulling it in here would cost this hook the instrumentation it
# exists to have already paid. Kept in step by
# ``test_run_affected_tests.py``'s marker-name test.
_TYPEGUARD_WARM_FAILED_MARKER: Final[str] = "typeguard-warm-FAILED"

# Above this many affected test files the local run stops being a fast screen
# and becomes a slower duplicate of CI, so the unit run is deferred whole.
#
# Derived, not guessed, and RE-derived after the first value proved optimistic.
# The budget is unchanged: the rest of the pre-push hook costs ~145s, which
# leaves ~155s of the 300s budget for pytest. What changed is the measured cost
# of a file. Two samples now exist, and they disagree with a linear model:
#
#   862 files (nine packages)                            218s -> 0.25s/file
#   600 files (api + observability + settings + tools)   218s -> 0.36s/file
#   333 files (api alone, quiet machine)                 207s -> 0.62s/file
#
# Same wall-clock, wildly different counts, because ``-n 8 --dist=loadfile``
# pins a file to one worker and the run ends when the BUSIEST worker does. All
# three selections carry ``tests/unit/api``, so all three pay its serial tail
# and none is predicted by its own file count. The first sample set the cap at
# ~600 on the 0.25s rate; a settings-hub change then selected exactly 600, ran
# 218s rather than the budgeted 155s, and took the push to 321s, admitted by
# one file. The cap moved to 425 on the 0.36s rate; a comment-only edit to an
# api module then selected 333 and took the push to 427s.
#
# The third sample is the one that says what the other two could not, because
# it is that tail ON ITS OWN: ``tests/unit/api`` is 333 files and 207s, so a
# selection carrying it has already spent the whole 155s pytest budget before
# any other package is counted. No rate fixes that, because the cost is not
# per-file; the cap simply has to sit BELOW what api alone contributes, or the
# package that dominates every heavy selection is the one thing the cap cannot
# exclude. 155s / 0.62s puts it at 250, which does.
#
# So the cap is set from the pessimistic sample, and it is a ceiling on a weak
# predictor rather than a model of the cost. Below it the heavy packages stop
# dominating and file count starts to mean something; at 333 the local run
# already costs more than the ~186s whole-tier baseline, which is the
# definition of having stopped being a screen.
#
# File count rather than package count because packages differ by two orders of
# magnitude (``tests/unit/a2a`` against ``tests/unit/api``), so counting
# packages would defer a cheap nine-package change and admit an expensive
# two-package one.
#
# All-or-nothing on purpose. Running a subset means choosing which affected
# packages go unverified, and every rule for choosing is either arbitrary or
# perverse: dropping the largest drops the packages a broad change most affects.
# Deferring the whole run says one true thing (CI owns this one) instead of
# quietly verifying an unprincipled fraction of it.
_MAX_AFFECTED_TEST_FILES: Final[int] = 250

# The test tiers, each of which owns its own runner. Everything else under
# ``tests/`` is infrastructure the unit tier imports (``_shared/``,
# ``baselines/``, ``_typeguard_checker.py``), so it raises a whole-suite
# question rather than mapping to a package. A tier added later and not
# listed here lands on that side too, which announces a deferral it did not
# need instead of silently checking nothing.
_TEST_TIERS: Final[frozenset[str]] = frozenset(
    {"unit", "integration", "e2e", "conformance", "benchmarks", "evals", "evals_spine"}
)

# A test module imported by another test module, by dotted path. Both import
# forms are matched because both appear in the tree, and the trailing boundary
# stops ``tests.unit.meta.test_service`` from also matching a longer sibling
# whose name merely extends it.
_TEST_MODULE_IMPORT: Final[re.Pattern[str]] = re.compile(
    r"^(?:from|import)\s+(tests(?:\.[A-Za-z_][A-Za-z0-9_]*)*\.test_[A-Za-z0-9_]+)\b",
    re.MULTILINE,
)

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _prepush_scope import (  # type: ignore[import-not-found]
        MIN_MODULE_DEPTH,
        PYPROJECT,
        REPO_ROOT,
        SAFE_MODULE_NAME,
        TOP_LEVEL_SRC,
        GitError,
        announce_deferral,
        changed_files,
        classify_src_path,
        git_output,
        hooks_dir,
        merge_base,
    )
else:
    from scripts._prepush_scope import (
        MIN_MODULE_DEPTH,
        PYPROJECT,
        REPO_ROOT,
        SAFE_MODULE_NAME,
        TOP_LEVEL_SRC,
        GitError,
        announce_deferral,
        changed_files,
        classify_src_path,
        git_output,
        hooks_dir,
        merge_base,
    )

_REPO_ROOT = REPO_ROOT

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


def _is_test_file(name: str) -> bool:
    """Whether *name* is a file pytest collects as a test module.

    Mirrors pytest's ``python_files`` default, which the project leaves
    unset, and the glob :func:`count_affected_test_files` counts with.

    Returns:
        ``True`` for a ``test_*.py`` basename.
    """
    return name.startswith("test_") and name.endswith(".py")


def _classify_test_path(parts: tuple[str, ...]) -> tuple[str, str | None]:
    """Classify a path under ``tests/``.

    Anything outside a tier directory (``tests/_shared/``,
    ``tests/baselines/``, a bare ``tests/foo.py``) is infrastructure the
    unit tier imports without being part of it, so a change there is a
    whole-suite question. Deferring it says so; classifying it "other"
    would run nothing and announce nothing, which reads as a push with
    nothing to check. A tier directory that is not ``unit`` is that
    tier's own business and this runner never runs it.

    Returns:
        ``(category, module)``, where module names the ``tests/unit/``
        package the path belongs to (``"."`` for the tier root).
    """
    if len(parts) < MIN_MODULE_DEPTH or parts[1] not in _TEST_TIERS:
        return "shared_test_infra", None
    if parts[1] != "unit":
        return "other", None

    # The regex already rejects dotted names like test_smoke.py and
    # __init__.py, but listing them explicitly documents the intent.
    if SAFE_MODULE_NAME.match(parts[2]) and parts[2] not in TOP_LEVEL_SRC:
        if _is_test_file(parts[-1]):
            return "test_unit_file", parts[2]
        return "test_unit", parts[2]

    # Either a file sitting directly in the tier root, or a path whose
    # package component is not a package name at all (a ``..`` from a
    # crafted diff). Only the first is addressable, and only at exactly
    # that depth: past it the rejected component is a directory nobody
    # has validated, so the tier root's own smoke test is the answer.
    if len(parts) == MIN_MODULE_DEPTH and _is_test_file(parts[2]):
        return "test_unit_file", "."
    return "test_unit", "."


def _classify_path(parts: tuple[str, ...]) -> tuple[str, str | None]:
    """Classify a file path into a category and optional module name.

    Returns ``(category, module)`` where category is one of:
    ``"conftest"``, ``"blast_radius"``, ``"top_level_src"``,
    ``"src_module"``, ``"test_unit"``, ``"test_unit_file"``,
    ``"shared_test_infra"``, ``"other"``. For ``"test_unit_file"`` the
    module names the package the file sits in, which is what the caller
    de-duplicates against rather than what it selects.
    """
    if parts[-1] == "conftest.py":
        return "conftest", None

    source: tuple[str, str | None] | None = classify_src_path(parts)
    if source is not None:
        return source

    if parts[0] == "tests":
        return _classify_test_path(parts)

    return "other", None


@cache
def _test_module_importers() -> Mapping[str, frozenset[str]]:
    """Map each imported test module to the packages that import it.

    A test module is normally a leaf: pytest collects it and nothing else
    reads it, which is what makes selecting one file sufficient. A few
    carry a shared fake or helper that sibling test modules import by
    dotted path, and for those the leaf assumption is simply false -- a
    change to the definition can break importers the file-level selection
    would never run.

    Scanned once per process with a regex rather than parsed: the answer
    is needed only for the handful of changed test files in one push, and
    an import that this regex misses degrades to today's file-only
    selection rather than to a wrong package.

    Returns:
        Dotted module name -> the ``tests/unit/`` packages importing it.
    """
    importers: dict[str, set[str]] = {}
    for path in (_REPO_ROOT / "tests").rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError, UnicodeDecodeError:
            continue
        parts = path.relative_to(_REPO_ROOT).as_posix().split("/")
        if parts[1] != "unit":
            # Only the unit tier is this runner's to select; an importer in
            # another tier is that tier's runner to worry about.
            continue
        package = parts[2] if len(parts) > MIN_MODULE_DEPTH else "."
        for match in _TEST_MODULE_IMPORT.finditer(text):
            importers.setdefault(match.group(1), set()).add(package)
    return MappingProxyType(
        {name: frozenset(packages) for name, packages in importers.items()}
    )


def _importer_packages(filepath: str) -> frozenset[str]:
    """Return the ``tests/unit/`` packages importing the module at *filepath*.

    Returns:
        The importing packages, empty when nothing imports this module.
    """
    dotted = filepath.removesuffix(".py").replace("/", ".")
    return _test_module_importers().get(dotted, frozenset())


def _selected_test_files(module: str, filepaths: set[str]) -> list[str]:
    """Return the changed test files under ``tests/unit/<module>`` to run.

    A test file is the one thing in the tree with no importers, so
    running its siblings verifies nothing the change could have broken.
    Everything shared it depends on -- a ``conftest.py``, a helper
    module, the package's own source -- is classified elsewhere and
    still selects the whole package.

    A path that no longer exists is dropped rather than handed to
    pytest, which exits 4 on a missing path and would fail the push for
    the deletion itself. The containment check is the same barrier
    :data:`SAFE_MODULE_NAME` provides for directories: a ``..`` segment
    past the package name would otherwise carry a crafted diff path
    straight into the argv.

    Returns:
        Repo-relative paths, one per surviving file.
    """
    owner = (_REPO_ROOT / "tests" / "unit" / module).resolve()
    selected: list[str] = []
    for filepath in sorted(filepaths):
        candidate = (_REPO_ROOT / filepath).resolve()
        if candidate.is_relative_to(owner) and candidate.is_file():
            selected.append(str(candidate.relative_to(_REPO_ROOT)))
    return selected


def _affected_test_dirs(changed: list[str]) -> tuple[list[str], bool]:
    """Map changed files to test directories.

    Returns ``(test_dirs, deferred)`` where *deferred* records that a
    cross-tree question (a blast-radius module, shared test
    infrastructure, a top-level source file) was raised and handed to
    CI. The affected directories are still returned and still run: the
    local push verifies what changed, CI owns the sweep.
    """
    modules: set[str] = set()
    changed_test_files: dict[str, set[str]] = {}
    deferred = False

    for filepath in changed:
        parts = PurePosixPath(filepath).parts
        category, module = _classify_path(parts)

        if category in {
            "conftest",
            "blast_radius",
            "top_level_src",
            "shared_test_infra",
        }:
            deferred = True
        if module is None:
            continue
        if category == "test_unit_file":
            changed_test_files.setdefault(module, set()).add(filepath)
            # A test module that other test modules import is not a leaf:
            # its importers have to run too, or breaking a shared fake
            # passes a push having run only the file that defines it.
            modules.update(_importer_packages(filepath))
        else:
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

    # A package already selected covers its own test files; adding them
    # again would collect each one twice.
    for mod, filepaths in sorted(changed_test_files.items()):
        if mod not in modules:
            test_dirs.extend(_selected_test_files(mod, filepaths))

    return test_dirs, deferred


_BASELINE_PATH = _REPO_ROOT / "tests" / "baselines" / "unit_timing.json"


_PASSED_COUNT_RE = re.compile(r"(\d+)\s+passed")

# xdist prints worker-crash lines like
# ``worker 'gw3' crashed while running 'tests/foo.py::test_bar[2-2]'``
# when a worker process dies (segfault, abort, OS kill).  Used by the
# run classifier to tell native-level crashes apart from real test failures.
_WORKER_CRASH_RE = re.compile(
    r"worker '(?P<worker>\w+)' crashed while running '(?P<test>[^']+)'",
)

# A different xdist signature for the same underlying problem: when a
# worker terminates abnormally between tests (rather than during one),
# xdist prints ``[gwN] node down: Not properly terminated`` instead of
# the canonical ``worker 'gwN' crashed while running '...'`` form. The
# Python 3.14 + Windows ProactorEventLoop IOCP teardown race produces
# both signatures depending on exactly when the IOCP cleanup blew up,
# and both are native-level worker crashes that block the gate (a real
# defect to debug, not flakiness to wave through). Captures the worker id
# only -- there is no associated test id in this signature.
_NODE_DOWN_RE = re.compile(
    r"\[(?P<worker>gw\d+)\] node down: Not properly terminated",
)

# pytest in ``-q`` mode prints ``FAILED <test_id> - <reason>`` (or just
# ``FAILED <test_id>``) at the start of a line for every failure in the
# session summary.  ``\S+`` captures up to the first whitespace; valid
# pytest test ids never contain whitespace, so the boundary is safe.
_FAILED_RE = re.compile(r"^FAILED (?P<test>\S+)", re.MULTILINE)


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


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    """Kill *proc* and every xdist worker it spawned.

    ``proc.kill()`` only terminates the pytest master process; the 8
    xdist workers (each its own python subprocess) survive briefly as
    orphans until execnet's keepalive notices and they exit. On a
    watchdog-fired kill we want the whole tree gone immediately so the
    next push tick does not race against zombie workers still holding
    file locks / db connections.

    POSIX: ``subprocess.Popen(..., start_new_session=True)`` puts the
    master and its children in their own process group, then
    ``os.killpg(getpgid, SIGKILL)`` takes them all out at once.

    Windows: ``taskkill /F /T /PID`` walks the parent-child tree
    (Windows kernel records parent PIDs in EPROCESS) and force-
    terminates every descendant. More robust than
    ``send_signal(CTRL_BREAK_EVENT) + proc.kill()``, which only reaches
    direct children in the new process group and misses any subprocess
    that started its own group (some C-extensions running aiosqlite /
    docker calls do exactly that).
    """
    # A kill that fails is announced. The caller reports the hung-run exit
    # code either way, so a silent failure here reads as "the tree is gone"
    # while the workers are still holding the locks this exists to release.
    try:
        if sys.platform == "win32":
            killed = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
                timeout=_TASKKILL_TIMEOUT_SECONDS,
            )
            if killed.returncode != 0:
                # taskkill reports refusal through its exit code, not an
                # exception, so without this the announcement below never
                # fires for the commonest failure it exists to catch.
                print(
                    f"taskkill refused the pytest process tree at pid "
                    f"{proc.pid} (exit {killed.returncode}); workers may "
                    "still be running and holding file locks.",
                    file=sys.stderr,
                )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        # Already gone, which is the outcome this wanted.
        pass
    except (subprocess.SubprocessError, OSError) as exc:
        print(
            f"could not kill the pytest process tree at pid {proc.pid} "
            f"({type(exc).__name__}: {exc}); workers may still be running "
            "and holding file locks.",
            file=sys.stderr,
        )


def _own_process_group_kwargs() -> dict[str, object]:
    """Return the Popen kwargs that isolate pytest's process group.

    The pytest master and every xdist worker must share a group of their
    own so the watchdog can take the whole tree out atomically; without
    it ``proc.kill()`` reaches only the master and the workers survive as
    orphans for several seconds.

    Returns:
        Platform-appropriate Popen keyword arguments.
    """
    kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return kwargs


def _print_hung_run_banner(timeout_seconds: float) -> None:
    """Explain that the watchdog killed a wedged pytest run."""
    print(
        f"\n{'!' * 60}\n"
        f"run_affected_tests: pytest exceeded "
        f"{timeout_seconds:.0f}s wall-clock cap -- killing.\n"
        f"The pre-push hook gates ALL pushes; investigate which test\n"
        f"is hung (the worker traceback dumps above name it) and fix\n"
        f"the root cause -- there is no bypass.\n"
        f"{'!' * 60}",
        file=sys.stderr,
    )


def _tee_output(proc: subprocess.Popen[str]) -> tuple[int, str]:
    """Echo the process's output live while capturing it.

    ``subprocess.run`` + ``capture_output`` buffers everything until the
    process exits, hiding a multi-minute suite behind silence; the capture
    is still needed because the "N passed" summary feeds the per-test
    regression rail.

    Returns:
        The process's exit code and everything it wrote.
    """
    if proc.stdout is None:
        return proc.wait(), ""
    lines: list[str] = []
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        lines.append(line)
    return proc.wait(), "".join(lines)


def _stream_pytest(
    cmd: list[str], *, timeout_seconds: float | None = None
) -> tuple[int, str]:
    """Run *cmd* as pytest, tee stdout, and return ``(returncode, stdout)``.

    When ``timeout_seconds`` is set, a watchdog thread kills the entire
    pytest process group if the run lasts longer; this is the safety net
    for the Windows + Python 3.14 + xdist IOCP teardown hang that can
    leave a worker silently wedged for hours otherwise. On timeout the
    function returns ``(_PYTEST_HUNG_EXIT_CODE, captured)``. Callers MUST
    short-circuit on that code rather than forwarding to
    ``_classify_isolation_outcome``: the classifier would misread the
    killed run's partial stdout (worker-crash markers left by workers
    dying after the master vanished) rather than the canonical 124 signal.

    Returns:
        The run's exit code and captured output.
    """
    timeout_fired = False

    with subprocess.Popen(  # type: ignore[call-overload]  # **dict unpack can't match Popen overloads
        cmd,
        cwd=_REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        **_own_process_group_kwargs(),
    ) as proc:
        watchdog: threading.Timer | None = None
        if timeout_seconds is not None and timeout_seconds > 0:
            cap = timeout_seconds

            def _on_timeout() -> None:
                nonlocal timeout_fired
                timeout_fired = True
                _print_hung_run_banner(cap)
                _kill_process_tree(proc)

            watchdog = threading.Timer(cap, _on_timeout)
            watchdog.daemon = True
            watchdog.start()

        try:
            returncode, captured = _tee_output(proc)
        finally:
            if watchdog is not None:
                watchdog.cancel()

    return (_PYTEST_HUNG_EXIT_CODE if timeout_fired else returncode), captured


def _pytest_command(paths: list[str]) -> list[str]:
    """Build the pytest argv for *paths*.

    Inherits ``--dist loadfile`` from pyproject.toml's ``addopts`` so
    every test in a file stays on the same xdist worker: work-stealing
    rebalances tests across workers mid-run, and the resulting cumulative
    per-worker resource growth crashes workers on Python 3.14 + Windows.

    ``--max-worker-restart=0`` (matching CI) forbids restarting a worker
    that crashes, so a native crash always surfaces as a failed run rather
    than being silently recovered.

    Returns:
        The argv to hand to :func:`_stream_pytest`.
    """
    return [
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


def _grade_run(
    returncode: int,
    captured_stdout: str,
    *,
    elapsed: float,
    run_all: bool,
) -> int:
    """Turn a finished pytest run into the script's exit code.

    The timing rail is skipped whenever the run itself was adverse: a
    worker crash charges the time spent before the crash against the
    surviving test count, so ``elapsed / test_count`` reports a
    regression that is not there, and stacking that banner on top of a
    crash dump buries the actual root cause.

    Returns:
        The exit code the push should see.
    """
    outcome = _classify_isolation_outcome(returncode, captured_stdout)
    effective_returncode = outcome.exit_code
    if effective_returncode == 0 and _check_timing_regression(
        elapsed,
        run_all=run_all,
        test_count=_parse_test_count(captured_stdout),
    ):
        return max(effective_returncode, 1)
    if outcome.kind == "regression":
        _print_isolation_banner(outcome)
    return effective_returncode


def _run_pytest(paths: list[str], *, run_all: bool = False) -> int:
    """Run pytest over *paths* and grade the result.

    Returns:
        The pytest exit code, or ``_PYTEST_HUNG_EXIT_CODE`` when the
        watchdog had to kill a wedged run.
    """
    start = time.monotonic()  # lint-allow: clock-seam -- gate script, no DI
    timeout_seconds = (
        _PYTEST_FULL_SUITE_TIMEOUT_SECONDS
        if run_all
        else _PYTEST_AFFECTED_TIMEOUT_SECONDS
    )
    returncode, captured_stdout = _stream_pytest(
        _pytest_command(paths), timeout_seconds=timeout_seconds
    )
    if returncode == _PYTEST_HUNG_EXIT_CODE:
        # The banner is already printed; the classifier would misread the
        # killed run's partial output instead of the canonical 124 signal.
        return _PYTEST_HUNG_EXIT_CODE
    # lint-allow: clock-seam -- gate script, no DI
    elapsed = time.monotonic() - start
    return _grade_run(returncode, captured_stdout, elapsed=elapsed, run_all=run_all)


@dataclass(frozen=True)
class IsolationOutcome:
    """Classified result of the affected-test pytest run.

    ``kind`` captures the verdict; ``exit_code`` is what the script should
    return.  ``crashed_tests`` / ``failed_tests`` carry the supporting evidence
    so the banner can name names.

    Invariants (enforced in ``__post_init__``):

    * ``pass`` -- ``exit_code == 0`` and every evidence tuple is empty.
    * ``regression`` -- ``exit_code >= 1``.  Covers real test failures AND
      worker crashes: a crashed xdist worker is a real defect to debug
      (teardown race, native deadlock), never an advisory pass. Evidence
      tuples may both be empty when pytest exits non-zero with degraded output
      the parser cannot interpret -- we fail closed rather than silently pass.

    Enforcing these at construction means the banner can rely on the
    invariant without re-checking at the print site.
    """

    kind: Literal["pass", "regression"]
    exit_code: int
    crashed_tests: tuple[str, ...] = field(default_factory=tuple)
    failed_tests: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Reject illegal ``(kind, exit_code, evidence)`` combinations."""
        if self.kind == "pass":
            if self.exit_code != 0:
                msg = f"pass outcome must have exit_code=0, got {self.exit_code}"
                raise ValueError(msg)
            if self.crashed_tests or self.failed_tests:
                msg = "pass outcome must carry no evidence tuples"
                raise ValueError(msg)
        elif self.kind == "regression" and self.exit_code == 0:
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
    """Decide whether the affected-test run is a regression or pass.

    The interesting axis is *real failure* vs *native crash*.  xdist
    marks a crashed test ``FAILED`` as collateral, so a ``FAILED``
    line that points at a crashed test is filtered out -- the crash
    is the real signal.

    Decision tree:

    * Any real failure (``FAILED`` for a non-crashed test) -> regression.
    * Any xdist worker crash -- a one-off crash, or a bare ``node down``
      (regardless of returncode) -> regression.  A crashed worker is a
      real defect (Python 3.14 + Windows ProactorEventLoop teardown
      race, native deadlock, memory corruption), never an advisory to
      wave through.  The gate blocks and names the crashed test(s) /
      worker(s) so the operator reads the dump and fixes the teardown.
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

    if real_failures:
        return IsolationOutcome(
            kind="regression",
            exit_code=max(returncode, 1),
            crashed_tests=crashed_tests,
            failed_tests=real_failures,
        )
    if crashes:
        return IsolationOutcome(
            kind="regression",
            exit_code=max(returncode, 1),
            crashed_tests=crashed_tests,
        )
    # A worker that went ``node down`` is a native-level crash, not a
    # test failure -- and a real defect to debug, never an advisory
    # pass. The real-failure and worker-crash checks above already
    # returned, so the worker death itself is the only adverse signal
    # here, and it blocks regardless of returncode. ``--max-worker-
    # restart=0`` forbids recovery, and a worker killed mid-teardown can
    # die after its tests passed -- leaving returncode 0 -- yet that is
    # the dominant Python 3.14 + Windows teardown-race shape and is
    # still a crash to debug, so a zero exit must not wave it through.
    # The loadscope crash guard in ``tests/conftest.py``
    # (``_install_xdist_loadscope_crash_guard``) suppresses the
    # downstream ``INTERNALERROR>``, and the worker dies before pytest
    # prints a FAILED summary, so the bare node-down is the only signal
    # -- surface the worker ids and block. The test names are
    # unrecoverable from a bare node-down. ``max(returncode, 1)`` keeps
    # the regression exit non-zero even when pytest itself returned 0.
    if node_down_workers:
        return IsolationOutcome(
            kind="regression",
            exit_code=max(returncode, 1),
            crashed_tests=tuple(f"<worker {w}>" for w in node_down_workers),
        )
    if returncode == 0:
        return IsolationOutcome(kind="pass", exit_code=0)
    return IsolationOutcome(kind="regression", exit_code=returncode)


def _print_isolation_banner(outcome: IsolationOutcome) -> None:
    """Print the right diagnostic banner for *outcome*'s kind.

    Two banners:

    * ``regression`` -- the operator must investigate; the gate blocks.
      Distinguishes a real test failure, a worker crash (native teardown
      race / deadlock to debug from the dump), and degraded non-zero
      output, so the message points at the right cause.
    * ``pass`` -- nothing to print.
    """
    if outcome.kind == "pass":
        return
    border = "!" * 60
    if outcome.kind == "regression":
        if outcome.failed_tests:
            body = (
                "TEST FAILURE: the affected-test run reported a failing test.\n"
                f"Failed: {', '.join(outcome.failed_tests)}\n"
                "Fix the failure. The gate has no bypass."
            )
        elif outcome.crashed_tests:
            body = (
                "WORKER CRASH: an xdist worker crashed during the run.\n"
                "A crashed worker is a real defect -- a Python 3.14 + Windows\n"
                "ProactorEventLoop teardown race, a native deadlock, or memory\n"
                "corruption -- NOT an advisory to wave through.\n"
                "Read the faulthandler / core dump and inspect the named\n"
                "test's teardown and fixtures for the root cause.\n"
                f"Crashed: {', '.join(outcome.crashed_tests)}\n"
                "Fix the root cause. The gate has no bypass and no advisory."
            )
        else:
            # Fail-closed: pytest exited non-zero but emitted no parsable
            # FAILED or worker-crash signal.  Don't silently pass; surface
            # the raw exit code and ask the operator to inspect the run.
            body = (
                "TEST-RUN REGRESSION: pytest exited non-zero "
                f"({outcome.exit_code}) with output the gate could not\n"
                "parse.  Inspect the captured pytest output above for\n"
                "the underlying failure.  The gate has no bypass."
            )
        print(f"\n{border}\n{body}\n{border}", file=sys.stderr)
        return


def _resolve_changed_files() -> list[str] | None:
    """Return changed files, or ``None`` when the file list is unknowable.

    ``None`` means git could not say what changed (no merge-base, shallow
    history). Nothing can be scoped from that, so the caller falls back to
    the whole suite: a push whose only local signal is "we could not tell"
    is worse than a slow one.

    Returns:
        The changed files, or ``None`` when git could not report them.
    """
    try:
        base = merge_base()
        changed: list[str] = changed_files(base)
    except GitError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
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
    porcelain = git_output("status", "--porcelain", strip=False)
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
    except GitError as exc:
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
        git_output("restore", "--", *newly_dirtied)
    except GitError as exc:
        print(
            f"run_affected_tests: FAILED to revert hook-modified files "
            f"({exc}). The working tree is left dirty; fix the writer "
            f"or revert manually before pushing.",
            file=sys.stderr,
        )
        return 1
    return 0


def _defer_to_ci(reason: str, *, scoped_run_follows: bool) -> None:
    """Announce that the full-suite question is CI's to answer."""
    announce_deferral(
        reason,
        deferred_scope="the full unit suite",
        ci_job="Test Unit shards",
        ran_locally="the affected tests still run here",
        scoped_run_follows=scoped_run_follows,
    )


def count_affected_test_files(test_dirs: Sequence[str]) -> int:
    """Count the test files the affected run would collect.

    A cheap stand-in for its wall-clock cost, taken before pytest starts so the
    budget decision happens instead of the expensive run rather than after it.
    A path that names a file counts as one; a directory counts its ``test_*.py``
    descendants.

    Returns:
        The number of test files across *test_dirs*.
    """
    total = 0
    for entry in test_dirs:
        path = _REPO_ROOT / entry
        if path.is_dir():
            total += sum(1 for _ in path.rglob("test_*.py"))
        elif path.is_file():
            total += 1
    return total


def _pytest_config_changed(changed: Sequence[str]) -> bool:
    """Whether the push touches pytest's own configuration.

    ``pyproject.toml`` carries ``addopts``, the xdist settings, the plugin
    list and the marker registry alongside every dependency pin, so a
    change to it can alter how the suite runs without any ``.py`` file
    appearing in the diff.

    Returns:
        ``True`` when ``pyproject.toml`` is among the changed files.
    """
    return PYPROJECT in changed


def _run_tests() -> int:
    """Select and run the affected unit tests.

    Returns:
        The pytest exit code (0 when nothing maps to a test directory).
    """
    changed = _resolve_changed_files()
    if changed is None:
        # The file list is unknowable, so nothing can be scoped. Run the
        # whole suite rather than nothing: the alternative is a push whose
        # only local signal is a message saying it was not checked.
        print(
            "Changed-file list unavailable -- cannot scope the suite; "
            "running the FULL unit suite as a fail-safe.",
            file=sys.stderr,
        )
        return _run_pytest(["tests/unit/"], run_all=True)

    py_changed = [f for f in changed if f.endswith(".py")]
    test_dirs, deferred = _affected_test_dirs(py_changed)

    deferred_announced = False
    if _pytest_config_changed(changed):
        _defer_to_ci(
            "pyproject.toml changed (pytest configuration)",
            scoped_run_follows=bool(test_dirs),
        )
        deferred_announced = True
    elif deferred:
        _defer_to_ci(
            "Foundational module, conftest or shared test infrastructure changed",
            scoped_run_follows=bool(test_dirs),
        )
        deferred_announced = True

    if not test_dirs:
        # A deferral already stated whether anything runs locally; a
        # second no-op verdict on the same push would read as unrelated.
        if deferred_announced:
            return 0
        if not py_changed:
            print("No Python files changed -- skipping unit tests.")
            return 0
        print("Changed files don't map to any test directories -- skipping.")
        return 0

    affected_files = count_affected_test_files(test_dirs)
    if affected_files > _MAX_AFFECTED_TEST_FILES:
        _defer_to_ci(
            f"{affected_files} affected test files across "
            f"{len(test_dirs)} package(s) exceeds the "
            f"{_MAX_AFFECTED_TEST_FILES}-file local budget",
            scoped_run_follows=False,
        )
        return 0

    print(f"Running affected tests: {', '.join(test_dirs)}")
    started = time.monotonic()  # lint-allow: clock-seam -- gate script, no DI
    exit_code = _run_pytest(test_dirs)
    # lint-allow: clock-seam -- gate script, no DI
    print(f"pytest: {time.monotonic() - started:.1f}s")
    return exit_code


def _run_full_suite() -> int:
    """Run the whole unit suite with the timing-regression rail armed.

    The per-test and whole-suite timing guards only evaluate on a full
    run (a scoped subset has no comparable baseline), so this is the one
    entry point that arms them.

    Returns:
        The pytest exit code.
    """
    return _run_pytest(["tests/unit/"], run_all=True)


def _report_stale_typeguard_warm() -> None:
    """Warn once if the last detached typeguard warm failed, then clear it.

    The warm runs detached after a dependency sync, so its exit code goes
    nowhere. Without this, a repeatedly-failing warm is invisible and every
    test process silently pays the full instrumentation cost again: a
    mysteriously slow suite with no attribution, which is the problem the
    warm exists to remove reintroduced one layer down. A warning rather
    than a block, because a cold cache costs time and never correctness.
    """
    directory = hooks_dir()
    if directory is None:
        return
    marker = directory / _TYPEGUARD_WARM_FAILED_MARKER
    if not marker.is_file():
        return
    print(
        "NOTE: the background typeguard cache warm after your last dependency "
        "sync failed, so every test process here re-instruments the package. "
        f"See {directory}/mypy-rewarm-last.log; re-run with "
        "`uv run python scripts/warm_typeguard_cache.py`.",
        file=sys.stderr,
    )
    with contextlib.suppress(OSError):
        marker.unlink()


def main() -> int:
    """Entry point.

    Snapshots the set of already-dirty tracked files, runs the suite,
    then reverts only the files the run itself dirtied. The test exit
    code is preserved; a failed revert is folded in so an un-revertable
    mutation cannot pass silently.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full",
        action="store_true",
        help="run the whole unit suite with the timing-regression guards armed",
    )
    run = _run_full_suite if parser.parse_args().full else _run_tests
    _report_stale_typeguard_warm()

    try:
        before = _tracked_dirty_paths()
    except GitError as exc:
        # No pre-run snapshot means we cannot safely tell hook-induced
        # changes from the developer's own. Skip reconciliation rather
        # than risk reverting real work; the run still gates correctness.
        print(
            f"run_affected_tests: could not read pre-run git status "
            f"({exc}); worktree reconciliation disabled for this run.",
            file=sys.stderr,
        )
        return run()
    test_returncode = run()
    reconcile_returncode = _reconcile_worktree(before)
    return max(test_returncode, reconcile_returncode)


if __name__ == "__main__":
    sys.exit(main())
