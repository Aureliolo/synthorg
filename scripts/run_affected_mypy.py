#!/usr/bin/env python3
"""Pre-push hook: type-check the tree, preferring the mypy daemon.

A warm ``dmypy`` daemon keeps the whole build graph resident and re-checks the
entire tree in a couple of seconds, so the main daemon ignores which files
changed and always checks the full scope: narrowing saves nothing once the
graph is in memory, and full scope catches the cross-module breakage that
per-module scoping cannot see by construction. The daemon is also single
process, so the Windows worker defect described at ``_MYPY_WORKERS`` cannot
arise on this path at all.

Keeping a graph resident is what makes the daemon fast and is also what makes
it expensive: a few GB for the main scope and roughly half that again for
``scripts/``, most of it the third-party stub closure rather than this
codebase, so both figures grow with the dependency set rather than staying
put. The ``scripts/`` daemon is therefore not kept warm by default. It is
consulted only when it would earn its footprint: when the change could reach
that scope, or when it is already running, where the extra coverage is nearly
free. ``--warm``, ``--status`` and ``--stop`` manage that footprint by hand,
and a daemon left idle past ``_DAEMON_IDLE_TIMEOUT_SECONDS`` releases it
unprompted, so a session that ends without calling ``--stop`` (or is killed
outright) cannot strand a daemon on the machine indefinitely. That bound is
only ever applied when a daemon starts, so one already listening is stopped
first and rebound (``_adopt_idle_timeout``); otherwise the guarantee would
skip exactly the long-lived daemons it exists for.

The cold path runs when no daemon can answer (CI, an explicit opt-out, or a
daemon that failed). It uses git diff against origin/main to type-check only
the affected module directories (``src/synthorg/<module>/`` and the
corresponding ``tests/unit/<module>/`` and ``tests/integration/<module>/``),
because a cold full run costs several minutes. Only Python (``.py``) file
changes are considered; non-Python changes are ignored. The ``.mypy_cache/``
directory keeps subsequent cold runs faster with a warm cache.

A foundational module (core, config, observability) defines types imported
across the entire codebase, so a change there raises a whole-tree question.
Cold, that question costs minutes and a push is held to a five-minute budget,
so it is handed to CI's Type Check job and printed, never silently narrowed;
the module's own paths are still checked here. ``--full`` runs the CI scope
on demand.

That deferral is why the cold path is weaker than CI, which always checks the
full tree: a change whose only broken consumer lives in an untouched module
directory passes here and fails there. The daemon path does not have that gap
(it always checks the full scope), so it only applies to a run that opted out
of the daemon or fell back from it. A clean opted-out run is not a promise
that CI will be clean.

Exit codes match mypy: 0 (no errors/nothing to check), 1 (type errors found), etc.
"""

import argparse
import contextlib
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Final, Literal, NamedTuple

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _prepush_scope import (  # type: ignore[import-not-found]
        MIN_MODULE_DEPTH,
        PYPROJECT,
        REPO_ROOT,
        SAFE_MODULE_NAME,
        GitError,
        announce_deferral,
        changed_files,
        classify_src_path,
        hooks_dir,
        merge_base,
    )
else:
    from scripts._prepush_scope import (
        MIN_MODULE_DEPTH,
        PYPROJECT,
        REPO_ROOT,
        SAFE_MODULE_NAME,
        GitError,
        announce_deferral,
        changed_files,
        classify_src_path,
        hooks_dir,
        merge_base,
    )

_REPO_ROOT = REPO_ROOT

# Full-tree mypy scope, mirroring the CI type-check job so a full local
# run catches the same surface CI does. evals/, docker/, and the root
# d2_fence.py are type-clean and included. scripts/ is type-checked
# separately by ``_run_scripts_mypy`` (it needs different flags).
_FULL_SCOPE: Final[list[str]] = ["src/", "tests/", "evals/", "docker/", "d2_fence.py"]

# Test subdirectories whose module layout the cold path can map to a narrow
# mypy target. Any other ``tests/<kind>/`` directory defers to CI instead: an
# unrecognised kind must never classify as "other", because that path yields
# no mypy targets at all and lets the gate exit 0 having checked nothing.
# Failing toward "someone checks it, and say so" keeps a new tests/
# subdirectory safe by default rather than silently unguarded until someone
# updates this tuple.
_TEST_KINDS = ("unit", "integration")

# mypy's ``--num-workers`` parallel build spawns worker subprocesses that talk
# to the parent over an IPC channel: a Windows named pipe, a POSIX socketpair.
# The Windows named-pipe path is defective: a worker's pipe end is gone when the
# parent writes the source set to it, so the parent's WriteFile aborts with
# WinError 233 (ERROR_PIPE_NOT_CONNECTED) and mypy dies with an INTERNAL ERROR
# on type-clean code. The worker count is the lever, not machine load: this
# repo's own measurements were "four workers fail reliably, two mostly pass,
# single-process always passes", which is the signature of a concurrency bug in
# mypy's Windows multi-worker IPC, not a starved CPU. (Why the worker process
# itself dies before the write is not isolated here -- it is a separate process
# whose failure mypy swallows -- but that does not matter once there is no
# worker.) So Windows runs single-process: zero workers, no pipe, WinError 233
# structurally impossible, and the full ~6.5k-file tree type-checks clean this
# way. POSIX uses a socketpair, does not hit the named-pipe defect, and keeps
# the faster workers.
_MYPY_WORKER_COUNT: Final[int] = 4
_MYPY_WORKERS: Final[list[str]] = (
    [] if sys.platform == "win32" else [f"--num-workers={_MYPY_WORKER_COUNT}"]
)

# mypy's parallel build spawns ``mypy.build_worker`` subprocesses that connect
# back to the parent. The worker's server and the parent's status poll each wait
# only ``WORKER_CONNECTION_TIMEOUT`` / ``WORKER_START_TIMEOUT`` (mypy/defaults.py);
# several fresh interpreters importing the compiled mypy package don't reliably
# win that window under the pre-push's process contention, so the connection
# drops and mypy aborts with an INTERNAL ERROR. Those timeouts are hardcoded with
# no env or flag override, so ``scripts/_mypy_worker_timeout/sitecustomize.py``
# widens them at interpreter startup; this directory goes on the mypy subprocess
# ``PYTHONPATH`` (workers inherit it) via ``_mypy_env``. Windows runs
# single-process (see ``_MYPY_WORKERS``), so this is inert there and takes effect
# for the POSIX socketpair workers.
_MYPY_TIMEOUT_SITECUSTOMIZE_DIR: Final[Path] = (
    _REPO_ROOT / "scripts" / "_mypy_worker_timeout"
)

# dmypy reports a completed check with mypy's own codes: 0 clean, 1 type errors
# found. Anything else means the daemon failed to answer (busy with a
# concurrent check, crashed, unusable state on disk), so the type result is
# unknown and the caller must re-check cold rather than report an answer it
# never received.
_CHECK_COMPLETED_CODES: Final[frozenset[int]] = frozenset({0, 1})

# Two attempts per daemon: exactly enough to absorb the one-invocation failure
# a stale status file causes (see ``_check_daemon``). More would only delay the
# cold fallback that a genuinely broken daemon needs.
_DAEMON_ATTEMPTS: Final[int] = 2

# Past this, a daemon check was a rebuild rather than an answer. A warm
# full-tree check measures ~1.4-4s; a cold rebuild measures 120-160s. The
# threshold sits far above the former and far below the latter, so it can
# neither cry wolf on a slow-but-warm machine nor miss a real rebuild.
_REBUILD_REPORT_SECONDS: Final[float] = 30.0

# How long a retry waits for a server that is still coming up before it
# issues a ``run`` of its own. dmypy waits 5s, which this repo's daemon
# exceeds on a loaded machine; the retry that follows is what starts a
# second server for one status file. Generous because the cost of waiting
# is a few idle seconds and the cost of not waiting is a leaked multi-GB
# server plus a status file that reports a graph nobody holds.
_DAEMON_START_GRACE_SECONDS: Final[float] = 60.0
_DAEMON_POLL_SECONDS: Final[float] = 0.5

# Opt out of the daemon for a single run. CI is opted out unconditionally: a
# fresh container pays the multi-minute cold build and is then discarded before
# any warm run repays it.
_DAEMON_OPT_OUT_VAR: Final[str] = "SYNTHORG_NO_DMYPY"

# Env values that read as "off" rather than as a set flag, so CI=false does not
# silently disable the daemon for anyone whose tooling sets it that way.
_FALSEY_ENV_VALUES: Final[frozenset[str]] = frozenset({"", "0", "false", "no"})

# Wall-clock ceilings. This script gates every push, so no subprocess may block
# it without bound: a deadlocked daemon or a wedged mypy would otherwise hang
# the push with no exit but Ctrl-C, which then leaves the stale status file
# that ``_check_daemon`` has to recover from. The sibling
# ``run_affected_tests.py`` bounds its pytest subprocess for the same reason.
# A type-check timing out is treated as "no verdict", never as a pass. The git
# calls are bounded by ``GIT_TIMEOUT_SECONDS`` in the shared scope module.
_PROCESS_QUERY_TIMEOUT_SECONDS: Final[int] = 30
# ``ProcessId,CommandLine``: a row shorter than this lost its command line.
_PROCESS_ROW_FIELDS: Final[int] = 2
# What may follow a path in a command line without extending it into a
# different path: a separator, a closing quote, or an argument break.
_PATH_BOUNDARY_CHARS: Final[str] = "\\/\"' \t"
# How a mypy daemon identifies itself in its own command line. The only
# process ``--stop-holder`` is willing to terminate.
_DAEMON_PROCESS_MARKER: Final[str] = "mypy.dmypy"

# Whether two paths differing only in case are the same path. A command line
# records a path as the process was launched with it, while the needle is
# resolved, so on Windows the two routinely differ in case (drive letter, a
# hand-typed path, a launcher that lower-cases) and an exact match would miss
# a real holder -- reporting "no process holds ..." for the stranded worktree
# this tooling exists to release. POSIX paths are genuinely case-sensitive,
# where folding would conflate two different directories.
_PATH_MATCH_IS_CASE_SENSITIVE: Final[bool] = sys.platform != "win32"
# Generous: a cold daemon build over ~6.5k files legitimately takes minutes on
# a contended machine, so this bounds a hang rather than pacing a slow build.
_MYPY_TIMEOUT_SECONDS: Final[int] = 1800

# Idle lifetime of the daemon process itself (dmypy's ``--timeout``), NOT a
# bound on any one check. A daemon outlives the shell that started it, holding
# its scope's graph resident (see ``_warm`` for the per-scope cost) plus an open
# handle on its worktree's interpreter; on Windows that handle makes the
# worktree undeletable, and ``git worktree remove`` fails with "Invalid
# argument", which looks nothing like its cause. Nothing can stop a daemon whose
# session was killed rather than exited, so the daemon has to expire on its own.
# Two hours outlasts a meeting or a lunch, so a warm daemon is rarely lost
# mid-session, while one left behind overnight always goes away.
#
# dmypy fixes this when the daemon process starts, so a daemon already
# listening never picks it up from a later ``run``; ``_adopt_idle_timeout``
# is what brings those under the bound.
_DAEMON_IDLE_TIMEOUT_SECONDS: Final[int] = 7200


class _Daemon(NamedTuple):
    """One dmypy daemon: the scope it checks and the flags it binds to.

    The two scopes cannot share a daemon, for two independent reasons. dmypy
    binds a daemon to the mypy flags it started with and restarts it whenever
    they change, so alternating flag sets over one status file would rebuild
    from cold every invocation. And the scopes are mutually exclusive anyway:
    resolving ``scripts/`` needs ``MYPYPATH`` at the repo root, under which
    ``src/synthorg/x.py`` resolves as both ``src.synthorg.x`` and
    ``synthorg.x``, which mypy rejects outright.
    """

    label: Literal["main", "scripts"]
    status_file: Path
    paths: tuple[str, ...]
    extra: tuple[str, ...]
    # Root MYPYPATH here so a flat directory resolves to canonical package
    # names rather than clashing on bare vs dotted spellings. Carrying the
    # path rather than a flag keeps the daemon self-describing: the caller
    # does not have to know which single directory a boolean stood for.
    mypypath: Path | None

    @property
    def lifetime_file(self) -> Path:
        """Companion marker naming the pid started under a bounded lifetime.

        dmypy's own status file records the pid but not the idle timeout it
        was started with, and there is no way to ask a running daemon. This
        is the missing half: written by this script when it starts one, so a
        daemon it did not start is recognisable as unbounded.
        """
        return self.status_file.with_suffix(".lifetime.json")


_MAIN_DAEMON: Final[_Daemon] = _Daemon(
    label="main",
    status_file=_REPO_ROOT / ".dmypy-main.json",
    paths=tuple(_FULL_SCOPE),
    extra=(),
    mypypath=None,
)

# ``--no-warn-unused-configs`` because ``warn_unused_configs`` is on
# project-wide but is only meaningful for a whole-tree run. Checking this scope
# alone reaches a different subset of the third-party overrides, leaving the
# rest (``d2``, ``openhands``, ``pdfplumber``, ``sentence_transformers``,
# ``skimage``, ``tree_sitter_language_pack``, ``xdist`` at the time of writing)
# reported unused; the exact set shifts with whichever synthorg modules the
# scripts entry points happen to import. Cold mypy prints that as a note and
# still exits 0, as does the very first ``dmypy run``, but every recheck
# against an already-running daemon counts it and exits 1 -- and after the
# first invocation every run is a recheck, so this would fail every push on an
# otherwise clean tree.
#
# The cost: an override that only ever matters to a package imported solely
# from here is now unpoliced, since the full-tree run does not cover scripts/
# and this run does not report it. Worth remembering if scripts/ ever grows a
# third-party dependency the rest of the tree does not share.
_SCRIPTS_DAEMON: Final[_Daemon] = _Daemon(
    label="scripts",
    status_file=_REPO_ROOT / ".dmypy-scripts.json",
    paths=("scripts/",),
    extra=("--explicit-package-bases", "--no-warn-unused-configs"),
    mypypath=_REPO_ROOT,
)

_ALL_DAEMONS: Final[tuple[_Daemon, ...]] = (_MAIN_DAEMON, _SCRIPTS_DAEMON)

# Enforced here rather than only in a test: a duplicate status file silently
# costs a full rebuild on every alternating invocation, and a duplicate label
# would make the two indistinguishable in --status and --stop output.
_DUPLICATE_STATUS_FILE = "each daemon needs its own dmypy status file"
_DUPLICATE_LABEL = "each daemon needs a distinct label"
if len({daemon.status_file for daemon in _ALL_DAEMONS}) != len(_ALL_DAEMONS):
    raise AssertionError(_DUPLICATE_STATUS_FILE)
if len({daemon.label for daemon in _ALL_DAEMONS}) != len(_ALL_DAEMONS):
    raise AssertionError(_DUPLICATE_LABEL)

# Kilobytes per megabyte, for the RSS readings ``ps`` and ``tasklist`` report.
_KB_PER_MB: Final[int] = 1024

# dmypy's catch-all exit code for "the client could not get an answer": a dead
# daemon, a busy one, an unusable status file, or an internal error all land
# here, which is why the message text has to disambiguate them.
_DMYPY_FAILED: Final[int] = 2

# Substrings dmypy uses when it means "no daemon is running" rather than "the
# daemon is there but could not serve this request".
_ABSENT_DAEMON_MARKERS: Final[tuple[str, ...]] = (
    "no status file",
    "not running",
    "daemon has died",
    "no such file",
)


def _classify_path(
    parts: tuple[str, ...],
) -> tuple[str, str | None, str | None]:
    """Classify a file path for mypy target selection.

    Returns ``(category, module, test_path)`` where category is one of:
    ``"conftest"``, ``"blast_radius"``, ``"top_level_src"``,
    ``"src_module"``, ``"test_module"``, ``"test_file"``, ``"other"``.
    """
    if parts[-1] == "conftest.py":
        return "conftest", None, None

    source = classify_src_path(parts)
    if source is not None:
        category, module = source
        return category, module, None

    if parts[0] == "tests":
        if len(parts) >= MIN_MODULE_DEPTH and parts[1] in _TEST_KINDS:
            # Direct test file (e.g. tests/unit/test_smoke.py).
            if parts[2].endswith(".py"):
                return "test_file", None, f"tests/{parts[1]}/{parts[2]}"
            if SAFE_MODULE_NAME.match(parts[2]):
                return "test_module", None, f"tests/{parts[1]}/{parts[2]}"
        # Everything else under tests/ (tests/e2e, tests/conformance,
        # tests/benchmarks, a shallow tests/foo.py, an unsafe directory name)
        # has no narrow mapping. Deferring is the only safe answer:
        # classifying it "other" would drop it from the target set and let the
        # gate pass having type-checked nothing (see _TEST_KINDS).
        return "blast_radius", None, None

    return "other", None, None


def _paths_for_module(mod: str) -> list[str]:
    """Return existing src + test paths for a source module."""
    result: list[str] = []
    src_dir = _REPO_ROOT / "src" / "synthorg" / mod
    if src_dir.is_dir():
        result.append(f"src/synthorg/{mod}")
    for kind in _TEST_KINDS:
        test_dir = _REPO_ROOT / "tests" / kind / mod
        if test_dir.is_dir():
            result.append(f"tests/{kind}/{mod}")
    return result


def _affected_mypy_paths(changed: list[str]) -> tuple[list[str], bool]:
    """Map changed files to mypy target directories.

    Returns ``(paths, deferred)`` where *deferred* records that a
    cross-tree question was raised and handed to CI. The affected paths
    are still returned and still checked.
    """
    src_modules: set[str] = set()
    test_paths: set[str] = set()
    deferred = False

    for filepath in changed:
        parts = PurePosixPath(filepath).parts
        category, module, test_path = _classify_path(parts)

        if category in {"conftest", "blast_radius", "top_level_src"}:
            deferred = True
        if module is not None:
            src_modules.add(module)
        if test_path is not None:
            test_paths.add(test_path)

    # Build mypy target paths (only dirs that exist).
    paths: list[str] = []
    for mod in sorted(src_modules):
        paths.extend(_paths_for_module(mod))

    # Also include directly-changed test dirs/files not covered by src_modules.
    # Path traversal is prevented by _SAFE_MODULE_NAME validation in _classify_path.
    for tp in sorted(test_paths):
        if tp not in paths and (_REPO_ROOT / tp).exists():
            paths.append(tp)

    return paths, deferred


def _mypy_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Return an env for a mypy run with the worker-timeout sitecustomize wired.

    Prepends ``_MYPY_TIMEOUT_SITECUSTOMIZE_DIR`` to ``PYTHONPATH`` so the parent
    mypy interpreter (and every ``mypy.build_worker`` it spawns, which inherit
    ``os.environ``) widens the parallel-worker connection timeouts at startup. It
    is inert on Windows, which runs single-process, and effective for the POSIX
    socketpair workers (see ``_MYPY_WORKERS``).
    """
    env = dict(os.environ if base is None else base)
    sitecustomize_dir = str(_MYPY_TIMEOUT_SITECUSTOMIZE_DIR)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        sitecustomize_dir + os.pathsep + existing if existing else sitecustomize_dir
    )
    return env


def _invoke_mypy(
    paths: list[str],
    *,
    env: dict[str, str] | None = None,
    extra: list[str] | None = None,
) -> int:
    """Run mypy over *paths* and return its exit code.

    Uses ``--num-workers`` on POSIX (matching CI, socketpair IPC); Windows runs
    single-process, since the named-pipe worker IPC is defective there (see
    ``_MYPY_WORKERS``). No crash-retry: 0 (clean) and 1 (real type errors) are
    the only expected codes, and any other exit is a mypy INTERNAL ERROR that
    must surface and be fixed, not be papered over by blindly re-running.
    """
    extra = extra or []
    try:
        return subprocess.run(
            [sys.executable, "-m", "mypy", *_MYPY_WORKERS, *extra, *paths],
            cwd=_REPO_ROOT,
            check=False,
            env=_mypy_env(env),
            timeout=_MYPY_TIMEOUT_SECONDS,
        ).returncode
    except subprocess.TimeoutExpired:
        print(
            f"mypy exceeded {_MYPY_TIMEOUT_SECONDS}s on {' '.join(paths)} "
            "and was killed; treating as a failure.",
            file=sys.stderr,
        )
        return 2


def _run_mypy(paths: list[str]) -> int:
    """Run mypy with the given paths."""
    return _invoke_mypy(paths)


def _env_flag_set(name: str) -> bool:
    """Report whether env var *name* is set to something meaning "on"."""
    return os.environ.get(name, "").strip().lower() not in _FALSEY_ENV_VALUES


def _daemon_opted_out() -> bool:
    """Report whether this run must skip the mypy daemon."""
    return _env_flag_set(_DAEMON_OPT_OUT_VAR) or _env_flag_set("CI")


def _dmypy_result(
    daemon: _Daemon,
    *args: str,
    quiet: bool = False,
    timeout: int = _MYPY_TIMEOUT_SECONDS,
    kill_on_timeout: bool = True,
) -> subprocess.CompletedProcess[str] | None:
    """Run a dmypy subcommand, returning ``None`` if it hung and was killed.

    Only ``run`` can legitimately take minutes, so it keeps the build-sized
    default. The management subcommands pass a short *timeout*: ``stop``
    exists to reclaim memory before a heavy build, and a wedged daemon that
    made it block for the full build ceiling would defeat the point of
    calling it.

    ``_MYPY_WORKERS`` is deliberately absent. ``--num-workers`` is accepted by
    the daemon's argument parser but its server forces ``num_workers = 0``
    (fine-grained checking has no parallel mode), so passing it would only
    change the recorded flag set and trigger a needless restart. That single
    process is also why the Windows worker defect at ``_MYPY_WORKERS`` cannot
    arise on this path.
    """
    env = (
        {**os.environ, "MYPYPATH": str(daemon.mypypath)}
        if daemon.mypypath is not None
        else None
    )
    try:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "mypy.dmypy",
                "--status-file",
                str(daemon.status_file),
                *args,
            ],
            cwd=_REPO_ROOT,
            check=False,
            env=_mypy_env(env),
            capture_output=quiet,
            text=quiet,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        # The timeout killed the dmypy CLIENT this call spawned. The server
        # is a separate process and is still wedged, so retrying would walk
        # straight back into it for another full ceiling. Kill the server
        # too, and the retry gets a clean one. ``kill_on_timeout=False`` is
        # what the kill itself passes, so a kill that also hangs reports
        # rather than recursing.
        if kill_on_timeout:
            print(
                f"{daemon.label} daemon exceeded {timeout}s; killing the "
                f"server so the retry starts a fresh one",
                file=sys.stderr,
            )
            _kill_wedged_server(daemon)
        else:
            print(
                f"{daemon.label} daemon exceeded {timeout}s and was killed",
                file=sys.stderr,
            )
        return None


def _kill_wedged_server(daemon: _Daemon) -> None:
    """Hard-kill a daemon whose server stopped answering.

    ``subprocess.run(timeout=...)`` only reaches the client it started; the
    dmypy server is a separate, still-wedged process holding the status
    file. Left alone it absorbs every remaining attempt at the full build
    ceiling each time, so a single hung server could hold a push for hours
    while printing a remedy nobody is there to run. ``kill`` is issued
    directly rather than ``stop`` because a graceful stop queues behind the
    in-flight request that is already stuck.

    Best-effort by design: if the kill fails too, the caller still falls
    back to a cold check, which is slow but correct.
    """
    killed = _dmypy_result(
        daemon,
        "kill",
        quiet=True,
        timeout=_PROCESS_QUERY_TIMEOUT_SECONDS,
        kill_on_timeout=False,
    )
    if killed is None or killed.returncode != 0:
        print(
            f"{daemon.label}: could not kill the wedged server; "
            f"stop it by hand with: "
            f"dmypy kill --status-file {daemon.status_file}",
            file=sys.stderr,
        )
        return
    _forget_bounded_lifetime(daemon)


def _dmypy(
    daemon: _Daemon,
    *args: str,
    quiet: bool = False,
    timeout: int = _MYPY_TIMEOUT_SECONDS,
) -> int:
    """Run a dmypy subcommand for *daemon* and return its exit code.

    A killed-on-timeout run reports dmypy's own "something went wrong" code so
    callers cannot mistake a hang for a verdict.
    """
    result = _dmypy_result(daemon, *args, quiet=quiet, timeout=timeout)
    return _DMYPY_FAILED if result is None else result.returncode


class _LifetimeRecord(NamedTuple):
    """What this script remembers about a daemon it started.

    Two readers want different fields out of the same marker, and both a
    misparse and a renamed key fail the same silent way: the marker reads as
    absent, the daemon is restarted, and the only symptom is the push getting
    slower again. So the shape is named once here and parsed once, rather
    than re-derived at each read site.
    """

    pid: int
    idle_timeout_seconds: int
    dependency_digest: str | None

    def to_json(self) -> str:
        """Serialise the record for the marker file.

        Returns:
            The marker's contents.
        """
        return json.dumps(self._asdict())

    @classmethod
    def from_json(cls, text: str) -> _LifetimeRecord | None:
        """Parse a marker's contents.

        Args:
            text: Whatever the marker file held.

        Returns:
            The record, or None when the text is not a usable marker.
            Failing open would vouch for a daemon nothing verified.
        """
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(raw, dict):
            return None
        pid, idle = raw.get("pid"), raw.get("idle_timeout_seconds")
        digest = raw.get("dependency_digest")
        # ``bool`` subclasses ``int``, and a pid of ``True`` is not a pid.
        if type(pid) is not int or type(idle) is not int:
            return None
        if digest is not None and not isinstance(digest, str):
            return None
        return cls(pid=pid, idle_timeout_seconds=idle, dependency_digest=digest)


def _read_lifetime_record(daemon: _Daemon) -> _LifetimeRecord | None:
    """Read the marker recording how *daemon* was last started.

    Args:
        daemon: The daemon whose marker to read.

    Returns:
        The record, or None when no usable marker exists.
    """
    try:
        return _LifetimeRecord.from_json(
            daemon.lifetime_file.read_text(encoding="utf-8")
        )
    # A marker caught mid-write holds a partial UTF-8 sequence, which is a
    # decode failure rather than an IO one and would otherwise escape.
    except OSError, UnicodeDecodeError:
        return None


def _recorded_lifetime_pid(daemon: _Daemon) -> int | None:
    """Return the pid this script last started under the current lifetime.

    ``None`` whenever the marker is missing, unreadable, not an object, or
    records a different lifetime than the one now configured -- every case
    where the daemon that may be listening cannot be assumed to expire.
    Changing ``_DAEMON_IDLE_TIMEOUT_SECONDS`` therefore rebinds warm daemons
    on their next use rather than leaving them on the old value.
    """
    record = _read_lifetime_record(daemon)
    if record is None or record.idle_timeout_seconds != _DAEMON_IDLE_TIMEOUT_SECONDS:
        return None
    return record.pid


def _dependency_digest() -> str | None:
    """Fingerprint the resolved dependency set the graph was built against.

    ``uv.lock`` changes exactly when the installed packages can change, and
    it is one small file, so hashing it is far cheaper than inspecting
    site-packages.

    Returns:
        A hex digest, or None when the lock cannot be read (then staleness
        is simply not asserted, which is today's behaviour).
    """
    try:
        return hashlib.blake2b(
            (REPO_ROOT / "uv.lock").read_bytes(), digest_size=16
        ).hexdigest()
    except OSError:
        return None


def _recorded_dependency_digest(daemon: _Daemon) -> str | None:
    """Return the dependency digest recorded when this daemon was started."""
    record = _read_lifetime_record(daemon)
    return None if record is None else record.dependency_digest


def _record_bounded_lifetime(daemon: _Daemon) -> None:
    """Record that the daemon now listening was started under the bound."""
    pid = _daemon_pid(daemon)
    if pid is None:
        return
    payload = _LifetimeRecord(
        pid=pid,
        idle_timeout_seconds=_DAEMON_IDLE_TIMEOUT_SECONDS,
        dependency_digest=_dependency_digest(),
    ).to_json()
    try:
        daemon.lifetime_file.write_text(payload, encoding="utf-8")
    except OSError as exc:
        # Not fatal: the next invocation reads no marker and restarts the
        # daemon once more, which is wasteful but never wrong.
        print(
            f"could not record the {daemon.label} daemon's lifetime: {exc}",
            file=sys.stderr,
        )


def _forget_bounded_lifetime(daemon: _Daemon) -> None:
    """Drop the marker for a daemon that is no longer running.

    Pids are reused, so a marker outliving its process could vouch for an
    unrelated one that happened to land on the same number.
    """
    # A marker that cannot be removed only costs one extra restart.
    with contextlib.suppress(OSError):
        daemon.lifetime_file.unlink(missing_ok=True)


def _adopt_idle_timeout(daemon: _Daemon) -> None:
    """Stop a daemon predating the idle timeout so the next run rebinds it.

    dmypy fixes a daemon's idle lifetime at process start. A ``run`` against
    one already listening is only a check request, so ``--timeout`` never
    reaches it: a daemon started before this script passed the flag, or by a
    bare ``dmypy run`` typed by hand, lives forever. That is precisely the
    daemon that strands a worktree, so the guarantee is worth a restart.

    The marker names the pid this script last started under the current
    bound, so an unvouched pid is stopped once and comes back bounded. Cost:
    one graph rebuild, once per worktree -- the same cost a dependency sync
    already imposes, rather than something paid every push.
    """
    if _recorded_lifetime_pid(daemon) == _daemon_pid(daemon):
        return
    if not _daemon_running(daemon):
        return
    print(
        f"{daemon.label} daemon predates the "
        f"{_DAEMON_IDLE_TIMEOUT_SECONDS}s idle timeout and would outlive this "
        "session; restarting it once so it cannot (the rebuild is slow, and "
        "happens only this once)."
    )
    _dmypy_result(daemon, "stop", quiet=True, timeout=_PROCESS_QUERY_TIMEOUT_SECONDS)
    _forget_bounded_lifetime(daemon)


def _drop_stale_graph(daemon: _Daemon) -> None:
    """Stop a daemon whose resident graph predates the installed packages.

    A ``uv sync`` rewrites site-packages without stopping the daemon, so the
    graph it holds is invalid while the daemon still answers. dmypy notices
    only once the next check is already underway, and then silently pays the
    full cold rebuild inside it: measured at 124s against 1.4s warm, i.e. a
    third of the push budget spent with nothing on screen explaining why.

    Detecting it here turns that into an announced restart before the check
    starts. The cost is identical (one rebuild either way); what changes is
    that it is attributable, and that the caller is not left wondering
    whether the push has hung.

    Only reached when a daemon is actually listening, so this never starts
    one. A daemon this script has not vouched for carries no recorded digest
    and is left to ``_adopt_idle_timeout``.
    """
    recorded = _recorded_dependency_digest(daemon)
    if recorded is None:
        return
    current = _dependency_digest()
    if current is None:
        # Every other degraded path in this file announces itself. Staying
        # quiet here would disable the one guard that turns a silent
        # mid-check rebuild into an announced restart, silently.
        print(
            f"{daemon.label}: could not read uv.lock; the daemon's graph was "
            "not checked for staleness this run.",
            file=sys.stderr,
        )
        return
    if current == recorded:
        return
    if not _daemon_running(daemon):
        return
    print(
        f"{daemon.label} daemon was built against a different uv.lock "
        "(dependencies changed since it started); restarting it now rather "
        "than paying the rebuild silently inside the next check."
    )
    _dmypy_result(daemon, "stop", quiet=True, timeout=_PROCESS_QUERY_TIMEOUT_SECONDS)
    _forget_bounded_lifetime(daemon)


def _check_daemon(daemon: _Daemon) -> int | None:
    """Check *daemon*'s scope, returning ``None`` if it gave no verdict.

    Uses ``run`` rather than ``check`` so the daemon starts on first use and
    restarts itself whenever the mypy configuration changes. ``--timeout``
    binds the started daemon's idle lifetime (see
    ``_DAEMON_IDLE_TIMEOUT_SECONDS``); it is a daemon-management flag rather
    than a mypy flag, so it does not count towards the flag set whose change
    forces a rebuild. It only ever reaches a daemon at start, which is what
    ``_adopt_idle_timeout`` exists to arrange for one already running.

    A daemon killed without cleaning up (a reboot, a machine-wide process
    sweep) leaves a status file pointing at a dead pid. dmypy reports "Daemon
    has died" and fails that invocation, but does start a replacement, so the
    attempt after it succeeds. Retrying here rather than degrading to a cold
    run costs the same wall clock either way and leaves a warm daemon behind
    instead of nothing. The retry is safe when the daemon is merely busy:
    ``run`` starts a daemon only when none is listening, so a second attempt
    competes for the existing one and falls through to cold if it loses. A
    retry first waits for any server the previous attempt left starting
    (``_wait_for_daemon``): dmypy's own five-second ceiling is shorter than
    this repo's daemon needs on a loaded machine, and issuing ``run`` before
    the starting server publishes its status file is what starts a second
    one and strands the first.

    A second process can be in the same window at the same time (a
    detached ``--rewarm`` after a dependency sync, and a push), so the
    whole sequence runs under ``_start_lock``.

    See docs/reference/retry-patterns.md: Pattern C/Sync -- this script is a
    standalone pre-push hook that must run without importing synthorg, so the
    shared GeneralRetryHandler is not available to it.
    """
    # Started before the lock is acquired, so waiting out the grace period and
    # the restarts that precede the first check are charged against the same
    # ceiling. A deadline opened after the lock would hand a run that already
    # waited its full allowance on top of the wait.
    deadline = time.monotonic() + _MYPY_TIMEOUT_SECONDS
    with _start_lock(daemon):
        return _check_daemon_locked(daemon, deadline)


def _check_daemon_locked(daemon: _Daemon, deadline: float) -> int | None:
    """Run the daemon check itself, inside the start lock.

    Args:
        daemon: The daemon to check.
        deadline: Monotonic instant the whole check must be finished by.

    Returns:
        The dmypy exit code, or ``None`` when it gave no verdict.
    """
    # Before anything else: a leaked server from an earlier run holds GBs and
    # is unreachable by stop/kill, and the status file it lost says nothing
    # about it. One cached process-table read covers both daemons.
    _reap_orphaned_servers(daemon)
    _adopt_idle_timeout(daemon)
    _drop_stale_graph(daemon)
    last_code: int | None = None
    for attempt in range(_DAEMON_ATTEMPTS):
        if attempt:
            # The previous attempt may have left a server still starting up.
            # Issuing ``run`` before it publishes its status file is what
            # starts a second one and strands the first.
            _wait_for_daemon(daemon)
        started = time.monotonic()
        # One ceiling for the whole daemon, not one per attempt: two
        # independent full-length allowances would let a wedged daemon hold
        # a push for twice the bound the ceiling exists to impose, and the
        # scripts daemon would then start its own from zero.
        remaining = deadline - started
        # A sub-second remainder truncates to ``timeout=0`` below, which fires
        # the wedged-server kill against a daemon that was never given a
        # chance to answer, and reports it as "exceeded 0s".
        if remaining < 1:
            break
        code = _dmypy(
            daemon,
            "run",
            "--timeout",
            str(_DAEMON_IDLE_TIMEOUT_SECONDS),
            "--",
            *daemon.paths,
            *daemon.extra,
            timeout=int(remaining),
        )
        if _report_if_rebuilt(daemon, time.monotonic() - started):
            # A rebuild means the graph this run started from was gone, so
            # re-read the table: the twin that took the status file may have
            # appeared after this run's snapshot.
            _reap_orphaned_servers(daemon, fresh=True)
        if code in _CHECK_COMPLETED_CODES:
            _record_bounded_lifetime(daemon)
            if attempt:
                print(
                    f"{daemon.label} daemon needed a restart before it could "
                    "answer; run --status if this repeats.",
                    file=sys.stderr,
                )
            return code
        last_code = code
    print(
        f"{daemon.label} daemon gave no verdict after {_DAEMON_ATTEMPTS} "
        f"attempts (last exit {last_code}).",
        file=sys.stderr,
    )
    return None


def _report_if_rebuilt(daemon: _Daemon, elapsed: float) -> bool:
    """Say so when a daemon check clearly rebuilt rather than answered.

    ``_drop_stale_graph`` can only pre-empt the staleness it knows how to
    fingerprint. A resident graph goes stale for other reasons too: a
    branch switch or rebase rewrites every source mtime, a mypy config
    change makes dmypy restart itself, and site-packages can move without
    ``uv.lock`` moving. One such push was measured at 162s in this hook
    with ``uv.lock`` untouched since hours before the daemon started, and
    it took a dozen probes to establish that afterwards, because the
    rebuild is indistinguishable from a slow check from the outside.

    Enumerating every trigger is not tractable; naming the cost is. A warm
    check is seconds, so anything past the threshold below is a rebuild,
    and saying so turns an unexplained slow push into a labelled event
    with an obvious remedy.

    Returns:
        Whether the check was slow enough to be a rebuild.
    """
    if elapsed < _REBUILD_REPORT_SECONDS:
        return False
    print(
        f"{daemon.label} daemon took {elapsed:.0f}s: that is a full graph"
        " rebuild, not a check. Its resident graph was stale for a reason"
        " the uv.lock fingerprint does not cover (a rebase or branch"
        " switch rewrites every source mtime; a mypy config change makes"
        " dmypy restart itself). The graph is warm again now, so the next"
        " push pays seconds; `make typecheck-warm` pre-pays it off the"
        " push budget.",
        file=sys.stderr,
    )
    return True


def _daemon_running(daemon: _Daemon) -> bool:
    """Report whether *daemon* is alive, without starting it.

    dmypy exits non-zero both for a dead daemon and for a live one that could
    not answer, so a busy daemon reads as "not running" here. That only ever
    costs an optional extra scope check, never a wrong verdict, but the
    distinguishing text dmypy prints is surfaced so a wedged daemon does not
    stay invisible.
    """
    result = _dmypy_result(
        daemon, "status", quiet=True, timeout=_PROCESS_QUERY_TIMEOUT_SECONDS
    )
    if result is None:
        return False
    if result.returncode == 0:
        return True
    if not _reports_absent_daemon(result):
        print(
            f"{daemon.label} daemon did not answer: "
            f"{_first_line(result.stderr) or _first_line(result.stdout)}",
            file=sys.stderr,
        )
    return False


def _first_line(text: str | None) -> str:
    """Return the first non-empty line of *text*, or the empty string."""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _reports_absent_daemon(result: subprocess.CompletedProcess[str]) -> bool:
    """Report whether dmypy said the daemon is absent rather than unwell.

    dmypy uses one exit code for both, so the message text is the only signal
    separating "nothing is running" from "running but wedged".
    """
    combined = f"{result.stdout or ''}{result.stderr or ''}".lower()
    return any(marker in combined for marker in _ABSENT_DAEMON_MARKERS)


def _daemon_pid(daemon: _Daemon) -> int | None:
    """Return *daemon*'s process id from its status file, if it has one.

    Tolerates every shape a half-written or foreign status file can take:
    unreadable, not JSON, or JSON that simply is not an object.
    """
    try:
        raw = json.loads(daemon.status_file.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    pid = raw.get("pid")
    # ``bool`` subclasses ``int``, and a pid of ``True`` is not a pid.
    if type(pid) is not int:
        return None
    return pid


def _process_rss_mb(pid: int) -> int | None:
    """Return a process's resident memory in MB, or ``None`` if unreadable.

    Shells out rather than taking a psutil dependency for one status line.
    Both back ends report kilobytes; Windows formats them with a thousands
    separator whose character is locale-dependent, so every non-digit is
    stripped before parsing. That separator can be a comma, which is also
    ``tasklist``'s CSV delimiter, so the row is parsed as real CSV rather than
    split on commas: the quoted memory field must survive intact.
    """
    command = (
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"]
        if sys.platform == "win32"
        else ["ps", "-o", "rss=", "-p", str(pid)]
    )
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=_PROCESS_QUERY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"could not read memory for pid {pid}: {exc}", file=sys.stderr)
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    if sys.platform == "win32":
        rows = [row for row in csv.reader(result.stdout.splitlines()) if row]
        if not rows:
            return None
        field = rows[0][-1]
    else:
        field = result.stdout
    digits = re.sub(r"[^0-9]", "", field)
    return int(digits) // _KB_PER_MB if digits else None


def _process_parent(pid: int) -> int | None:
    """Return a process's parent pid, or ``None`` if unreadable.

    Single-pid lookup in the style of :func:`_process_rss_mb` rather than a
    third column on :func:`_process_table`: only the orphan check needs
    lineage, and widening that table would change the shape every caller and
    its tests read.
    """
    command = (
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').ParentProcessId",
        ]
        if sys.platform == "win32"
        else ["ps", "-o", "ppid=", "-p", str(pid)]
    )
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=_PROCESS_QUERY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"could not read the parent of pid {pid}: {exc}", file=sys.stderr)
        return None
    parent = result.stdout.strip()
    return int(parent) if result.returncode == 0 and parent.isdigit() else None


@cache
def _process_table_snapshot() -> tuple[tuple[int, str], ...]:
    """One process-table read per run, shared by the orphan checks.

    Enumerating processes shells out (PowerShell on Windows), so reading
    it once per daemon per call would put a measurable cost on every push
    for a check that is looking for the same processes each time.

    Returns:
        The process table as of the first call in this process.
    """
    return tuple(_process_table())


def _orphaned_servers(daemon: _Daemon, *, fresh: bool = False) -> list[int]:
    """Return dmypy servers bound to *daemon* that its status file disowns.

    A status file holds one pid, and the last server to start wins it. When
    two start for the same file, the loser keeps running and keeps whatever
    graph it built: gigabytes resident, unreachable by ``stop`` or ``kill``
    (both address the status file), and invisible to ``--status``. The winner
    is typically the one that built nothing, so every later check rebuilds
    while every marker agrees the daemon is warm.

    The launcher this venv interposes between ``dmypy`` and the real
    interpreter carries the same command line as the server it spawned, so
    the status pid's parent is excluded too; without that it would be reaped
    on every single run.

    Args:
        daemon: The daemon whose status file bounds the search.
        fresh: Re-read the process table instead of using this run's
            snapshot. Only worth it right after something is known to have
            changed, such as a check that turned out to be a rebuild.

    Returns:
        Pids of servers for this status file that nothing references.
    """
    status_pid = _daemon_pid(daemon)
    if status_pid is None:
        return []
    table = _process_table() if fresh else _process_table_snapshot()
    referenced = {status_pid}
    if any(pid == status_pid for pid, _command in table):
        # There is a live daemon to protect, so its launcher has to be
        # identified before anything is killed: the launcher carries the
        # same command line, and reaping it would fire on every run.
        parent = _process_parent(status_pid)
        if parent is None:
            print(
                f"{daemon.label}: could not identify the running daemon's "
                "launcher; skipping the orphan check rather than risk "
                "reaping it.",
                file=sys.stderr,
            )
            return []
        referenced.add(parent)
    needle = str(daemon.status_file.resolve())
    return [
        pid
        for pid, command in table
        if pid not in referenced
        and _DAEMON_PROCESS_MARKER in command
        and _references_path(command, needle)
    ]


def _reap_orphaned_servers(daemon: _Daemon, *, fresh: bool = False) -> None:
    """Kill every dmypy server for *daemon* that its status file disowns.

    Announced rather than silent: an orphan is proof that a run's status
    file was overwritten mid-start, so the graph the caller believes is
    resident is not, and the next check will rebuild.

    The status file is re-read immediately before each kill. A server that
    was an orphan when the table was read can be the legitimate one by the
    time its turn comes (it published its status file in between), and
    killing it would tear down a check some other process is waiting on.

    Args:
        daemon: The daemon whose orphans to reap.
        fresh: Passed through to :func:`_orphaned_servers`.
    """
    for pid in _orphaned_servers(daemon, fresh=fresh):
        if _daemon_pid(daemon) == pid:
            continue
        rss = _process_rss_mb(pid)
        if rss is None:
            # Exited between the table read and now, which is the outcome
            # this wanted; saying so would read as a failure.
            continue
        print(
            f"{daemon.label}: reaping dmypy pid {pid} holding {rss}MB. It is "
            "bound to this daemon's status file but is not the process that "
            "file names, so nothing can reach it and no check can reuse its "
            "graph.",
            file=sys.stderr,
        )
        _stop_holder(pid)


@contextlib.contextmanager
def _start_lock(daemon: _Daemon) -> Iterator[bool]:
    """Serialise the decide-to-start window across processes.

    ``_wait_for_daemon`` only orders one process's own retries. Two
    processes racing is the commoner case and is reachable by design:
    ``rewarm_caches_after_sync.sh`` detaches a rebuild after a ``uv
    sync`` that takes minutes, and nothing stops a ``git push`` starting
    while it runs. Each independently reads the status file, sees no
    daemon, and starts one; the loser is then stranded.

    Advisory and fail-open on purpose. A lock that could block a push is
    worse than the race it prevents, so waiting is bounded and a lock
    that cannot be taken is proceeded past. A lock older than the build
    ceiling belonged to a process that died holding it, and is taken
    over rather than waited on forever.

    Yields:
        Whether the lock was held. ``False`` means the caller raced.
    """
    lock = daemon.status_file.with_suffix(".start.lock")
    deadline = time.monotonic() + _DAEMON_START_GRACE_SECONDS
    held = False
    while time.monotonic() < deadline:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _lock_is_stale(lock) and _remove_stale_lock(lock):
                continue
            # Either the lock is live, or the takeover failed and retrying
            # it immediately would spin the whole grace period at full tilt.
            time.sleep(_DAEMON_POLL_SECONDS)
            continue
        except OSError:
            break  # Unwritable location; the lock is not worth failing over.
        with contextlib.suppress(OSError):
            os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        held = True
        break
    try:
        yield held
    finally:
        if held:
            with contextlib.suppress(OSError):
                lock.unlink(missing_ok=True)


def _remove_stale_lock(lock: Path) -> bool:
    """Take over a lock whose owner died, reporting whether it worked.

    Windows refuses to unlink a file another process still holds open, so a
    takeover can fail against a lock that only looked abandoned. Failing is
    not fatal: the caller waits it out like a live lock, and the whole
    mechanism is advisory anyway.

    Args:
        lock: The lock file to remove.

    Returns:
        Whether the lock is now gone.
    """
    try:
        lock.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _lock_is_stale(lock: Path) -> bool:
    """Whether *lock* outlived any run that could still be holding it.

    Returns:
        ``True`` when the lock is older than a full build could take.
    """
    try:
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return False
    return age > _MYPY_TIMEOUT_SECONDS


def _pid_is_live(pid: int) -> bool:
    """Report whether *pid* names a running process.

    Args:
        pid: The process id to look for.

    Returns:
        ``True`` when the process table still carries it.
    """
    return any(running == pid for running, _command in _process_table())


def _wait_for_daemon(daemon: _Daemon) -> bool:
    """Wait for a starting daemon to publish its status file.

    ``dmypy run`` decides whether to start a server by reading that file, and
    a server still coming up has not written it. dmypy gives it five seconds;
    on a loaded machine this repo's daemon needs longer, and past the ceiling
    the client exits non-zero. Retrying straight away is what starts the
    second server, so the retry waits here first: if the daemon appears, the
    next ``run`` attaches to it instead of racing it.

    A pid in the file is not the condition to wait on, and treating it as one
    made this wait a no-op in the case it exists for. dmypy reports "Daemon
    has died" precisely BECAUSE the file still names the dead server, so the
    first poll saw a pid, returned at once, and the retry raced the
    replacement it was supposed to wait for: two servers 18 seconds apart,
    the one holding the graph orphaned, and the rebuild the push had just
    paid for thrown away. What is waited on is therefore a pid that can
    actually be reached: the one already there if it is alive, otherwise a
    different one, which is what a replacement publishes when it comes up.

    Polls the status file rather than asking dmypy: the file changing is the
    exact condition ``run`` branches on, and it is a file read. Asking dmypy
    would spawn a client per poll, each with its own multi-second timeout,
    which is both slower than the thing being waited for and a hundred
    processes over one wait. Liveness is read once, at entry, for the same
    reason: a pid that is dead now stays dead, so re-checking an unchanged
    one per poll would buy a process-table read a second and answer nothing.

    Returns:
        ``True`` once a reachable daemon is published, ``False`` at the
        ceiling.
    """
    stale_pid = _daemon_pid(daemon)
    if stale_pid is not None and _pid_is_live(stale_pid):
        # Busy rather than dead: no replacement is coming, and waiting for a
        # different pid would burn the whole grace period to no purpose.
        return True
    deadline = time.monotonic() + _DAEMON_START_GRACE_SECONDS
    while time.monotonic() < deadline:
        if _published_replacement(daemon, stale_pid):
            return True
        time.sleep(_DAEMON_POLL_SECONDS)
    return _published_replacement(daemon, stale_pid)


def _published_replacement(daemon: _Daemon, stale_pid: int | None) -> bool:
    """Report whether the status file now names a server other than *stale_pid*.

    Args:
        daemon: The daemon whose status file to read.
        stale_pid: The pid the file carried before the wait, or ``None`` when
            it carried none.

    Returns:
        ``True`` when a different server has published itself.
    """
    current = _daemon_pid(daemon)
    return current is not None and current != stale_pid


def _run_daemon_pass(changed: list[str] | None) -> int | None:
    """Type-check through the daemons, returning ``None`` to fall back cold.

    The main daemon always checks the full tree. The ``scripts/`` daemon costs
    another ~1.5GB resident, so it is only consulted when it would earn that:
    when the change could affect it, or when it happens to be warm already, in
    which case the extra coverage is nearly free. *changed* is ``None`` when
    git could not be read, which resolves toward checking more, not less.
    """
    main_code = _check_daemon(_MAIN_DAEMON)
    if main_code is None:
        return None

    if not _scripts_in_scope(changed) and not _daemon_running(_SCRIPTS_DAEMON):
        print("scripts/ unaffected and its daemon is cold -- skipping that scope.")
        return main_code

    scripts_code = _check_daemon(_SCRIPTS_DAEMON)
    if scripts_code is None:
        return None
    return max(main_code, scripts_code)


def _scripts_in_scope(changed: list[str] | None) -> bool:
    """Report whether this change could alter the ``scripts/`` verdict.

    A fifth of ``scripts/`` imports ``synthorg``, so a foundational change
    reaches it even when no file under ``scripts/`` was touched.
    """
    if changed is None:
        return True
    if any(f.startswith("scripts/") for f in changed):
        return True
    _, run_all = _affected_mypy_paths(changed)
    return run_all


def _run_scripts_mypy() -> int:
    """Type-check ``scripts/`` cold, with the flags its flat layout needs.

    Mirrors the second invocation in the CI type-check job, and shares its flag
    set with the daemon path so both report the same verdict.
    """
    env = {**os.environ, "MYPYPATH": str(_REPO_ROOT)}
    return _invoke_mypy(
        list(_SCRIPTS_DAEMON.paths), env=env, extra=list(_SCRIPTS_DAEMON.extra)
    )


def _run_full() -> int:
    """Run mypy across the whole tree, including the ``scripts/`` pass.

    Not reached by the pre-push path, which is scoped to what changed:
    this is the on-demand ``--full`` run, matching what CI's Type Check
    job covers.

    Returns:
        The worst exit code of the two passes.
    """
    return max(_run_mypy(list(_FULL_SCOPE)), _run_scripts_mypy())


def _parse_args() -> argparse.Namespace:
    """Parse the daemon-management flags.

    With no flag the script is the pre-push hook and checks the tree.

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Type-check the tree, preferring the mypy daemon."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--warm",
        action="store_true",
        help="build the main daemon now so later checks take seconds",
    )
    group.add_argument(
        "--rewarm",
        action="store_true",
        help=(
            "rebuild the main daemon's graph, but only if it is already "
            "resident (for use after a dependency sync invalidates it)"
        ),
    )
    group.add_argument(
        "--full",
        action="store_true",
        help="run the cold CI scope now, without consulting a daemon",
    )
    group.add_argument(
        "--stop",
        action="store_true",
        help="stop this worktree's daemons and reclaim their memory",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="show each daemon's state and resident memory",
    )
    group.add_argument(
        "--find-holders",
        metavar="PATH",
        help="list the processes holding PATH open (read-only)",
    )
    group.add_argument(
        "--stop-holder",
        metavar="PID",
        type=int,
        help="terminate one process named by --find-holders",
    )
    return parser.parse_args()


def _resolve_changed_files() -> list[str] | None:
    """Return the changed files, or ``None`` if git could not say.

    The full list, not just ``.py``: a ``pyproject.toml``-only change alters
    how mypy runs (its own config, the third-party override block, dependency
    pins) with no ``.py`` file in the diff, so the caller must see it to defer
    rather than silently report nothing to do.

    Returns:
        The changed paths, or ``None`` when git could not report them.
    """
    try:
        changed: list[str] = changed_files(merge_base())
    except GitError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return None
    return changed


def _warm() -> int:
    """Build the main daemon's graph now, so later checks are seconds.

    Only the main daemon: the ``scripts/`` daemon costs another ~1.5GB
    resident and starts by itself the first time a change reaches that scope.
    This blocks for several minutes by design; detach it from the caller (the
    worktree helper does) rather than backgrounding it here, so a build failure
    still surfaces somewhere.
    """
    print(f"Warming the {_MAIN_DAEMON.label} daemon (several minutes, once).")
    code = _check_daemon(_MAIN_DAEMON)
    if code is None:
        print("Daemon failed to build.", file=sys.stderr)
        return 2
    _status()
    return code


def _rewarm_marker() -> Path | None:
    """Return the path of the failed-re-warm marker, or ``None`` if unknown.

    Returns:
        The marker path, or ``None`` when the hooks directory is unknown.
    """
    directory = hooks_dir()
    return None if directory is None else directory / "mypy-rewarm-FAILED"


def report_stale_rewarm_failure() -> None:
    """Warn once if the last detached re-warm failed, then clear the marker.

    The re-warm runs detached, so its exit code goes nowhere and its log is
    only read by someone who already suspects this hook. Without this the
    failure mode is a mysteriously slow next type check with no attribution --
    precisely the problem the re-warm exists to remove, reintroduced one layer
    down. This is the same idea as the pre-push ``<hook>-FAILED`` marker,
    scaled to a warning rather than a block: a stale graph costs time, never
    correctness, so it must not stop anyone working.
    """
    marker = _rewarm_marker()
    if marker is None or not marker.is_file():
        return
    print(
        "NOTE: the background mypy re-warm after your last dependency sync "
        f"failed, so this check may pay a full cold rebuild. See {marker.parent}"
        "/mypy-rewarm-last.log.",
        file=sys.stderr,
    )
    with contextlib.suppress(OSError):
        marker.unlink()


def _rewarm() -> int:
    """Rebuild the main daemon's graph, but only if it is already resident.

    A ``uv sync`` rewrites the interpreter's site-packages, which invalidates
    the resident graph without stopping the daemon: the next check silently
    pays the full cold rebuild (measured at 124s against 1.4s warm), and if
    that next check is the pre-push hook it eats a third of the push budget.
    Re-warming right after the sync moves that cost off the push.

    Guarded on the daemon already running, which is the whole point of a
    separate mode. Warming unconditionally would start a ~2.5GB daemon in
    every worktree a sync ever touched, and several open worktrees would then
    cost more memory than the machine has spare -- exactly why the worktree
    helper refuses to warm at creation. This only ever restores a warm state
    that already existed.

    A failure drops a marker rather than only printing, because this runs
    detached: nothing reads its exit code and nothing reads its log unless
    told to. ``report_stale_rewarm_failure`` surfaces it on the next check.
    """
    if not _daemon_running(_MAIN_DAEMON):
        print(f"{_MAIN_DAEMON.label} daemon not resident; nothing to re-warm.")
        return 0
    print(f"Re-warming the {_MAIN_DAEMON.label} daemon after a dependency sync.")
    code = _check_daemon(_MAIN_DAEMON)
    marker = _rewarm_marker()
    if code is None:
        print("Daemon failed to rebuild its graph.", file=sys.stderr)
        if marker is not None:
            with contextlib.suppress(OSError):
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(
                    "the background re-warm after a dependency sync could not "
                    "rebuild the graph; see mypy-rewarm-last.log\n",
                    encoding="utf-8",
                )
        return 2
    if marker is not None:
        with contextlib.suppress(OSError):
            marker.unlink(missing_ok=True)
    return code


def _stop_one(daemon: _Daemon) -> tuple[int, str | None]:
    """Stop *daemon*, escalating to a hard kill if the graceful stop stalls.

    Returns ``(reclaimed_mb, failure_detail)``; *failure_detail* is ``None``
    when the daemon is gone by the end, however it got there.

    The escalation is what makes this reliable at session end. dmypy is a
    single-threaded request/response daemon, so a ``stop`` queues behind any
    in-flight ``run`` -- and a detached ``--rewarm`` triggered by an earlier
    ``uv sync`` can easily still be rebuilding. The graceful stop then times
    out, and a daemon that survives session end keeps holding a handle on this
    worktree's interpreter, which is what makes ``git worktree remove`` fail
    with an error that reads nothing like its actual cause. Reclaiming the
    memory and releasing the handle outrank letting an unattended rebuild
    finish, so a stalled stop is escalated rather than merely reported.

    Orphans go first, and for the same reason: ``stop`` and ``kill`` both
    address the status file, so neither can reach a server that lost it, and
    the largest process in the worktree would be exactly the one left holding
    the interpreter after a "stopped" verdict.
    """
    _reap_orphaned_servers(daemon)
    pid = _daemon_pid(daemon)
    rss = _process_rss_mb(pid) if pid is not None else None
    result = _dmypy_result(
        daemon, "stop", quiet=True, timeout=_PROCESS_QUERY_TIMEOUT_SECONDS
    )
    if result is not None and result.returncode == 0:
        _forget_bounded_lifetime(daemon)
        print(f"{daemon.label}: stopped")
        return rss or 0, None
    if result is not None and _reports_absent_daemon(result):
        _forget_bounded_lifetime(daemon)
        print(f"{daemon.label}: not running")
        return 0, None

    # A ``dmypy stop`` can fail with both streams empty, and the one line whose
    # job is to say why must not come out blank.
    detail = (
        _first_line(result.stderr) or _first_line(result.stdout)
        if result is not None
        else "timed out"
    ) or "no detail reported"
    killed = _dmypy_result(
        daemon, "kill", quiet=True, timeout=_PROCESS_QUERY_TIMEOUT_SECONDS
    )
    if killed is not None and (
        killed.returncode == 0 or _reports_absent_daemon(killed)
    ):
        _forget_bounded_lifetime(daemon)
        print(f"{daemon.label}: stopped (hard kill after: {detail})")
        return rss or 0, None
    return 0, detail


def _stop() -> int:
    """Stop every daemon in this worktree and report what it reclaimed.

    Returns non-zero if any daemon was there but refused to stop, so a caller
    reclaiming memory before a heavy build is not told it succeeded when the
    process is still resident.

    The daemons are stopped concurrently because they are independent, and
    sequentially they would cost two full ``_PROCESS_QUERY_TIMEOUT_SECONDS``
    windows back to back -- landing exactly on the SessionEnd hook's own
    ceiling, where the harness could kill this process before it had even
    attempted the second daemon or printed why the first failed.
    """
    reclaimed = 0
    failed = False
    with ThreadPoolExecutor(max_workers=len(_ALL_DAEMONS)) as pool:
        outcomes = list(pool.map(_stop_one, _ALL_DAEMONS))
    for daemon, (rss, detail) in zip(_ALL_DAEMONS, outcomes, strict=True):
        reclaimed += rss
        if detail is not None:
            failed = True
            print(
                f"{daemon.label}: stop FAILED -- {detail} "
                f"(try: dmypy kill --status-file {daemon.status_file})",
                file=sys.stderr,
            )
    if reclaimed:
        print(f"Reclaimed ~{reclaimed}MB.")
    return 1 if failed else 0


def _process_table() -> list[tuple[int, str]]:
    """Return every visible process as ``(pid, command line)``.

    Only the enumeration is platform-specific; the matching stays in Python so
    the rule that decides what gets killed is one testable function rather
    than a quoting-sensitive shell predicate. PowerShell is a data source here
    (CSV out), never the place a decision is made.
    """
    if sys.platform == "win32":
        command = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            (
                "Get-CimInstance Win32_Process"
                " | Select-Object ProcessId,CommandLine"
                " | ConvertTo-Csv -NoTypeInformation"
            ),
        ]
    else:
        command = ["ps", "-eo", "pid=,args="]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=_PROCESS_QUERY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"could not list processes: {exc}", file=sys.stderr)
        return []
    if result.returncode != 0:
        print(
            f"could not list processes: {_first_line(result.stderr)}", file=sys.stderr
        )
        return []
    parse = (
        _parse_windows_process_table
        if sys.platform == "win32"
        else _parse_posix_process_table
    )
    return list(parse(result.stdout))


def _parse_windows_process_table(output: str) -> Iterator[tuple[int, str]]:
    """Yield ``(pid, command line)`` from ``ConvertTo-Csv`` output.

    Parsed as real CSV rather than split on commas: a command line routinely
    contains them, and the quoted field must survive intact.

    Fed the whole text rather than pre-split lines, for the same reason. A
    command line may contain a newline, which CSV encodes as a quoted field
    spanning several lines; splitting first hands the reader a row with an
    unclosed quote, and every process after it is absorbed into that field
    instead of being yielded. What that costs is a table that looks complete
    and silently omits an arbitrary tail of the machine, so a stranded daemon
    holding gigabytes reports as "no process holds" and the operator is told
    the thing they can see in Task Manager does not exist.
    """
    rows = list(csv.reader(io.StringIO(output)))
    # ConvertTo-Csv emits a header row naming the selected properties.
    for row in rows[1:]:
        if len(row) < _PROCESS_ROW_FIELDS:
            continue
        pid, command = row[0].strip(), row[1].strip()
        if pid.isdigit():
            yield int(pid), command


def _parse_posix_process_table(output: str) -> Iterator[tuple[int, str]]:
    """Yield ``(pid, command line)`` from ``ps -eo pid=,args=`` output."""
    for line in output.splitlines():
        pid, _, command = line.strip().partition(" ")
        if pid.isdigit():
            yield int(pid), command.strip()


def _references_path(command: str, needle: str) -> bool:
    """Return True when *command* names *needle* as a whole path.

    A bare substring test is wrong in the dangerous direction: a worktree
    named ``foo`` is a prefix of a sibling named ``foo2``, so the sibling's
    daemon would be offered up for killing while it may be mid-push. Requiring
    a path separator, quote, or whitespace after the match settles that. A
    path genuinely nested under the needle still matches, which is correct --
    it does live there -- and is why the caller confirms each pid rather than
    acting on the list wholesale.

    Case handling follows the platform (see
    ``_PATH_MATCH_IS_CASE_SENSITIVE``), because a miss here is silent: the
    holder simply never appears in the listing.
    """
    if not _PATH_MATCH_IS_CASE_SENSITIVE:
        command, needle = command.casefold(), needle.casefold()
    start = 0
    while (index := command.find(needle, start)) != -1:
        end = index + len(needle)
        if end == len(command) or command[end] in _PATH_BOUNDARY_CHARS:
            return True
        start = index + 1
    return False


def _holders_of(path: Path) -> list[tuple[int, str]]:
    """Return the processes whose command line names *path*.

    Matched on the RESOLVED absolute path, so a bare directory name shared by
    two worktrees cannot conflate them: killing another worktree's daemon
    mid-push is a worse outcome than leaving this one behind.
    """
    needle = str(path.resolve())
    return [
        (pid, command)
        for pid, command in _process_table()
        if _references_path(command, needle)
    ]


def _find_holders(raw_path: str) -> int:
    """Print the processes holding *raw_path* open, touching none of them.

    Read-only by construction: this half exists so an operator can see exactly
    what would be killed before anything is. ``--stop-holder`` takes it from
    here, one explicit pid at a time.
    """
    path = Path(raw_path)
    if not path.exists():
        print(f"{raw_path} does not exist", file=sys.stderr)
        return 2
    holders = _holders_of(path)
    if not holders:
        print(f"no process holds {path.resolve()}")
        return 0
    print(f"{len(holders)} process(es) hold {path.resolve()}:")
    for pid, command in holders:
        print(f"  {pid}\t{command}")
    print("\nStop only the ones you recognise: --stop-holder <pid>")
    print("(that refuses any pid that is not a mypy daemon)")
    return 0


def _stop_holder(pid: int) -> int:
    """Terminate one named mypy daemon.

    Takes a single pid rather than a path: nothing here discovers what to
    kill, so a mistake in ``--find-holders`` cannot escalate into a kill on
    its own.

    The pid is then checked against the live process table and refused unless
    it really is a mypy daemon. Naming a process is the operator's
    confirmation, but a mistyped pid names some OTHER process perfectly well,
    and this exists to release a stranded daemon rather than to be a
    general-purpose process killer. Anything else is the operator's own tools
    to deal with.
    """
    holder = next(
        (command for running, command in _process_table() if running == pid), None
    )
    if holder is None:
        print(f"pid {pid} is not running", file=sys.stderr)
        return 2
    if _DAEMON_PROCESS_MARKER not in holder:
        print(
            f"pid {pid} is not a mypy daemon, refusing to stop it:\n  {holder}",
            file=sys.stderr,
        )
        return 2
    command = (
        ["taskkill", "/PID", str(pid), "/F"]
        if sys.platform == "win32"
        else ["kill", "-9", str(pid)]
    )
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=_PROCESS_QUERY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"could not stop pid {pid}: {exc}", file=sys.stderr)
        return 1
    if result.returncode != 0:
        detail = _first_line(result.stderr) or _first_line(result.stdout)
        print(f"could not stop pid {pid}: {detail}", file=sys.stderr)
        return 1
    print(f"stopped pid {pid}")
    return 0


def _status() -> int:
    """Report each daemon's state, resident memory, and any orphans.

    An orphan is reported here because it is the difference between "warm"
    and "reports warm, rebuilds anyway": the memory the operator is looking
    for is resident, just not in the process the status file names.
    """
    for daemon in _ALL_DAEMONS:
        if not _daemon_running(daemon):
            print(f"{daemon.label:<8} stopped")
            continue
        pid = _daemon_pid(daemon)
        rss = _process_rss_mb(pid) if pid is not None else None
        size = f"{rss}MB" if rss is not None else "size unknown"
        print(f"{daemon.label:<8} running  {size:>14}  {' '.join(daemon.paths)}")
        for orphan in _orphaned_servers(daemon):
            orphan_rss = _process_rss_mb(orphan)
            held = f"{orphan_rss}MB" if orphan_rss is not None else "size unknown"
            print(
                f"{'':<8} ORPHAN   {held:>14}  pid {orphan}: a second server "
                "took the status file; this graph is unreachable and the next "
                "check will rebuild. The next run reaps it."
            )
    return 0


def _defer_to_ci(reason: str, *, scoped_run_follows: bool) -> None:
    """Announce that the whole-tree check is CI's to answer."""
    announce_deferral(
        reason,
        deferred_scope="full-tree mypy",
        ci_job="Type Check",
        ran_locally="the affected paths are still checked here",
        scoped_run_follows=scoped_run_follows,
    )


def _run_scoped(py_changed: list[str]) -> int:
    """Type-check the paths the changed files map to.

    Returns:
        The worst mypy exit code across the scoped and scripts passes.
    """
    scripts_changed = any(f.startswith("scripts/") for f in py_changed)
    paths, deferred = _affected_mypy_paths(py_changed)

    if deferred:
        _defer_to_ci(
            "Foundational module or conftest changed",
            scoped_run_follows=bool(paths) or scripts_changed,
        )

    started = time.monotonic()  # lint-allow: clock-seam -- gate script, no DI
    exit_code = 0
    if paths:
        print(f"Running mypy on: {', '.join(paths)}")
        exit_code = _run_mypy(paths)
    elif not scripts_changed:
        print("Changed files don't map to any mypy targets -- skipping.")
        return 0

    if scripts_changed:
        print("scripts/ changed -- running scripts mypy.")
        exit_code = max(exit_code, _run_scripts_mypy())

    # lint-allow: clock-seam -- gate script, no DI
    print(f"mypy: {time.monotonic() - started:.1f}s")
    return exit_code


def _dispatch_management_flag(args: argparse.Namespace) -> int | None:
    """Run the subcommand *args* selected, or return ``None`` for a type check.

    Args:
        args: Parsed arguments.

    Returns:
        The subcommand's exit code, or ``None`` when none was requested.
    """
    if args.warm:
        return _warm()
    if args.rewarm:
        return _rewarm()
    if args.stop:
        return _stop()
    if args.status:
        return _status()
    # Presence, not truthiness: ``--stop-holder 0`` and ``--find-holders ""``
    # are falsy, and silently running an ordinary type check instead of the
    # subcommand the operator asked for is the wrong way to reject them.
    if args.find_holders is not None:
        return _find_holders(args.find_holders)
    if args.stop_holder is not None:
        return _stop_holder(args.stop_holder)
    if args.full:
        return _run_full()
    return None


def main() -> int:
    """Entry point.

    Returns:
        The mypy exit code (0 when nothing maps to a target).
    """
    args = _parse_args()
    dispatched = _dispatch_management_flag(args)
    if dispatched is not None:
        return dispatched

    # An ordinary check is the first thing anyone runs after a sync, so it is
    # where a failed background re-warm has to become visible.
    report_stale_rewarm_failure()

    changed = _resolve_changed_files()
    py_changed = None if changed is None else [f for f in changed if f.endswith(".py")]

    # Before the daemon, not after: a confirmed-empty diff needs no type check
    # at all, and consulting the daemon first would make a docs-only push pay
    # a full recheck, or a cold graph build if no daemon is up. An unreadable
    # diff (``None``) is not the same as an empty one and still gets checked.
    if py_changed is not None and not py_changed:
        if changed is not None and PYPROJECT in changed:
            # A config-only change alters how mypy runs with no .py in the
            # diff; that is a whole-tree question, announced not silently
            # dropped -- the same trigger run_affected_tests.py uses.
            _defer_to_ci(
                "pyproject.toml changed (mypy configuration)",
                scoped_run_follows=False,
            )
            return 0
        print("No Python files changed -- skipping mypy.")
        return 0

    if not _daemon_opted_out():
        daemon_code = _run_daemon_pass(py_changed)
        if daemon_code is not None:
            return daemon_code
        print(
            "mypy daemon did not return a verdict -- checking cold.",
            file=sys.stderr,
        )

    if py_changed is None:
        # Nothing can be scoped without a file list, and a cold full run is
        # the only answer left: reporting a green push that inspected no code
        # would be worse than the minutes it costs.
        print("Cannot read the diff -- running full mypy.", file=sys.stderr)
        return _run_full()

    return _run_scoped(py_changed)


if __name__ == "__main__":
    sys.exit(main())
