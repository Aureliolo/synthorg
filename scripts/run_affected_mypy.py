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
it expensive: roughly 2.5GB for the main scope and another 1.5GB for
``scripts/``, most of it the third-party stub closure rather than this
codebase. So the ``scripts/`` daemon is not kept warm by default. It is
consulted only when it would earn its footprint: when the change could reach
that scope, or when it is already running, where the extra coverage is nearly
free. ``--warm``, ``--status`` and ``--stop`` manage that footprint by hand.

The cold path runs when no daemon can answer (CI, an explicit opt-out, or a
daemon that failed). It uses git diff against origin/main to type-check only
the affected module directories (``src/synthorg/<module>/`` and the
corresponding ``tests/unit/<module>/`` and ``tests/integration/<module>/``),
because a cold full run costs several minutes. Only Python (``.py``) file
changes are considered; non-Python changes are ignored. Foundational modules
(core, config, observability) define types imported across the entire codebase,
so a change there widens to a full cold run. The ``.mypy_cache/`` directory
keeps subsequent cold runs faster with a warm cache.

Exit codes match mypy: 0 (no errors/nothing to check), 1 (type errors found), etc.
Git command failures fall back to running full mypy on the whole-tree scope
(``src/``, ``tests/``, ``evals/``, ``docker/``, ``d2_fence.py``).
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Final, NamedTuple

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Full-tree mypy scope, mirroring the CI type-check job so a full local
# run catches the same surface CI does. evals/, docker/, and the root
# d2_fence.py are type-clean and included. scripts/ is type-checked
# separately by ``_run_scripts_mypy`` (it needs different flags).
_FULL_SCOPE: Final[list[str]] = ["src/", "tests/", "evals/", "docker/", "d2_fence.py"]

# Modules imported by nearly everything -- changes here mean "full mypy".
_BLAST_RADIUS_MODULES = frozenset({"core", "config", "observability"})

# Top-level source files that aren't in a module directory.
_TOP_LEVEL_SRC = frozenset({"__init__.py", "constants.py"})

# Minimum path depth for src/synthorg/<module> or tests/<kind>/<module>.
_MIN_MODULE_DEPTH = 3

# Test subdirectories that mypy should cover.
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

# Valid Python package directory names (prevents path traversal).
_SAFE_MODULE_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

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

# Opt out of the daemon for a single run. CI is opted out unconditionally: a
# fresh container pays the multi-minute cold build and is then discarded before
# any warm run repays it.
_DAEMON_OPT_OUT_VAR: Final[str] = "SYNTHORG_NO_DMYPY"


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

    label: str
    status_file: Path
    paths: tuple[str, ...]
    extra: tuple[str, ...]
    # Root MYPYPATH at the repo so a flat directory resolves to canonical
    # package names rather than clashing on bare vs dotted spellings.
    repo_on_mypy_path: bool


_MAIN_DAEMON: Final[_Daemon] = _Daemon(
    label="main",
    status_file=_REPO_ROOT / ".dmypy-main.json",
    paths=tuple(_FULL_SCOPE),
    extra=(),
    repo_on_mypy_path=False,
)

# ``--no-warn-unused-configs`` because ``warn_unused_configs`` is on
# project-wide and is only meaningful for a whole-tree run: this scope
# structurally cannot import litellm, litestar and the rest, so their
# ``[[tool.mypy.overrides]]`` sections are always reported unused here. Cold
# mypy prints that as a note and still exits 0, but dmypy counts it and exits
# 1, which would fail every push on a clean tree.
_SCRIPTS_DAEMON: Final[_Daemon] = _Daemon(
    label="scripts",
    status_file=_REPO_ROOT / ".dmypy-scripts.json",
    paths=("scripts/",),
    extra=("--explicit-package-bases", "--no-warn-unused-configs"),
    repo_on_mypy_path=True,
)

_ALL_DAEMONS: Final[tuple[_Daemon, ...]] = (_MAIN_DAEMON, _SCRIPTS_DAEMON)

# Kilobytes per megabyte, for the RSS readings ``ps`` and ``tasklist`` report.
_KB_PER_MB: Final[int] = 1024


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

    is_deep = len(parts) >= _MIN_MODULE_DEPTH
    if is_deep and parts[0] == "src" and parts[1] == "synthorg":
        if parts[2] in _TOP_LEVEL_SRC or not _SAFE_MODULE_NAME.match(parts[2]):
            return "top_level_src", None, None
        return (
            ("blast_radius", None, None)
            if parts[2] in _BLAST_RADIUS_MODULES
            else ("src_module", parts[2], None)
        )

    if is_deep and parts[0] == "tests" and parts[1] in _TEST_KINDS:
        # Direct test file (e.g. tests/unit/test_smoke.py).
        if parts[2].endswith(".py"):
            return "test_file", None, f"tests/{parts[1]}/{parts[2]}"
        if _SAFE_MODULE_NAME.match(parts[2]):
            return "test_module", None, f"tests/{parts[1]}/{parts[2]}"

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

    Returns ``(paths, run_all)`` where *run_all* is True when a
    blast-radius module or shared infrastructure was touched.
    """
    src_modules: set[str] = set()
    test_paths: set[str] = set()

    for filepath in changed:
        parts = PurePosixPath(filepath).parts
        category, module, test_path = _classify_path(parts)

        if category in {"conftest", "blast_radius", "top_level_src"}:
            return [], True
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

    return paths, False


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
    return subprocess.run(
        [sys.executable, "-m", "mypy", *_MYPY_WORKERS, *extra, *paths],
        cwd=_REPO_ROOT,
        check=False,
        env=_mypy_env(env),
    ).returncode


def _run_mypy(paths: list[str]) -> int:
    """Run mypy with the given paths."""
    return _invoke_mypy(paths)


def _daemon_opted_out() -> bool:
    """Report whether this run must skip the mypy daemon."""
    return bool(os.environ.get(_DAEMON_OPT_OUT_VAR) or os.environ.get("CI"))


def _dmypy(daemon: _Daemon, *args: str, quiet: bool = False) -> int:
    """Run a dmypy subcommand for *daemon* and return its exit code.

    ``_MYPY_WORKERS`` is deliberately absent: the daemon is single process and
    ``--num-workers`` is not one of its flags, which is also why the Windows
    worker defect at ``_MYPY_WORKERS`` cannot arise on this path.
    """
    env = (
        {**os.environ, "MYPYPATH": str(_REPO_ROOT)}
        if daemon.repo_on_mypy_path
        else None
    )
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
    ).returncode


def _check_daemon(daemon: _Daemon) -> int | None:
    """Check *daemon*'s scope, returning ``None`` if it gave no verdict.

    Uses ``run`` rather than ``check`` so the daemon starts on first use and
    restarts itself whenever the mypy configuration changes.

    A daemon killed without cleaning up (a reboot, a machine-wide process
    sweep) leaves a status file pointing at a dead pid. dmypy reports "Daemon
    has died" and fails that invocation, but does start a replacement, so the
    attempt after it succeeds. Retrying here rather than degrading to a cold
    run costs the same wall clock either way and leaves a warm daemon behind
    instead of nothing. The retry is safe when the daemon is merely busy:
    ``run`` starts a daemon only when none is listening, so a second attempt
    competes for the existing one and falls through to cold if it loses.
    """
    for _attempt in range(_DAEMON_ATTEMPTS):
        code = _dmypy(daemon, "run", "--", *daemon.paths, *daemon.extra)
        if code in _CHECK_COMPLETED_CODES:
            return code
    return None


def _daemon_running(daemon: _Daemon) -> bool:
    """Report whether *daemon* is alive, without starting it."""
    return _dmypy(daemon, "status", quiet=True) == 0


def _daemon_pid(daemon: _Daemon) -> int | None:
    """Return *daemon*'s process id from its status file, if it has one."""
    try:
        raw = json.loads(daemon.status_file.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    pid = raw.get("pid")
    return pid if isinstance(pid, int) else None


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
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError:
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
    """Run mypy across the whole tree, including the ``scripts/`` pass."""
    return max(_run_mypy(list(_FULL_SCOPE)), _run_scripts_mypy())


def _parse_args() -> argparse.Namespace:
    """Parse the daemon-management flags.

    With no flag the script is the pre-push hook and checks the tree.
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
        "--stop",
        action="store_true",
        help="stop this worktree's daemons and reclaim their memory",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="show each daemon's state and resident memory",
    )
    return parser.parse_args()


def _changed_python_files() -> list[str] | None:
    """Return the changed ``.py`` files, or ``None`` if git could not say."""
    try:
        return [f for f in _changed_files(_merge_base()) if f.endswith(".py")]
    except _GitError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return None


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


def _stop() -> int:
    """Stop every daemon in this worktree and report what it reclaimed."""
    reclaimed = 0
    for daemon in _ALL_DAEMONS:
        pid = _daemon_pid(daemon)
        rss = _process_rss_mb(pid) if pid is not None else None
        if _dmypy(daemon, "stop", quiet=True) == 0:
            reclaimed += rss or 0
            print(f"{daemon.label}: stopped")
        else:
            print(f"{daemon.label}: not running")
    if reclaimed:
        print(f"Reclaimed ~{reclaimed}MB.")
    return 0


def _status() -> int:
    """Report each daemon's state and resident memory."""
    for daemon in _ALL_DAEMONS:
        if not _daemon_running(daemon):
            print(f"{daemon.label:<8} stopped")
            continue
        pid = _daemon_pid(daemon)
        rss = _process_rss_mb(pid) if pid is not None else None
        size = f"{rss}MB" if rss is not None else "size unknown"
        print(f"{daemon.label:<8} running  {size:>14}  {' '.join(daemon.paths)}")
    return 0


def main() -> int:
    """Entry point."""
    args = _parse_args()
    if args.warm:
        return _warm()
    if args.stop:
        return _stop()
    if args.status:
        return _status()

    py_changed = _changed_python_files()

    if not _daemon_opted_out():
        daemon_code = _run_daemon_pass(py_changed)
        if daemon_code is not None:
            return daemon_code
        print(
            "mypy daemon did not return a verdict -- checking cold.",
            file=sys.stderr,
        )

    if py_changed is None:
        print("Cannot read the diff -- running full mypy.", file=sys.stderr)
        return _run_full()
    if not py_changed:
        print("No Python files changed -- skipping mypy.")
        return 0

    scripts_changed = any(f.startswith("scripts/") for f in py_changed)
    paths, run_all = _affected_mypy_paths(py_changed)

    if run_all:
        print("Foundational module or conftest changed -- running full mypy.")
        return _run_full()

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

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
