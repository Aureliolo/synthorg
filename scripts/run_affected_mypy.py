#!/usr/bin/env python3
"""Pre-push hook: run mypy only on modules affected by changed files.

Uses git diff against origin/main to determine which source modules changed,
then type-checks only those module directories (``src/synthorg/<module>/`` and
corresponding ``tests/unit/<module>/`` and ``tests/integration/<module>/``).
Only Python (``.py``) file changes are considered; non-Python changes are ignored.

Foundational modules (core, config, observability) trigger a full mypy run
because they define types imported across the entire codebase. The ``.mypy_cache/``
directory keeps subsequent full runs fast with warm cache.

Exit codes match mypy: 0 (no errors/nothing to check), 1 (type errors found), etc.
Git command failures fall back to running full mypy on the whole-tree scope
(``src/``, ``tests/``, ``evals/``, ``docker/``, ``d2_fence.py``).
"""

import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Final

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

_MYPY_WORKERS: Final[list[str]] = ["--num-workers=4"]

# mypy's parallel build spawns ``mypy.build_worker`` subprocesses that connect
# back over a named pipe. The worker's server and the parent's status poll each
# wait only ``WORKER_CONNECTION_TIMEOUT`` / ``WORKER_START_TIMEOUT`` (10s on
# Windows, mypy/defaults.py); several fresh interpreters importing the compiled
# mypy package don't reliably win that window under the pre-push's process
# contention, so the pipe closes and the parent's source-broadcast write aborts
# mypy with an INTERNAL ERROR. Those timeouts are hardcoded with no env or flag
# override, so ``scripts/_mypy_worker_timeout/sitecustomize.py`` widens them at
# interpreter startup; this directory goes on the mypy subprocess ``PYTHONPATH``
# (workers inherit it) via ``_mypy_env``.
_MYPY_TIMEOUT_SITECUSTOMIZE_DIR: Final[Path] = (
    _REPO_ROOT / "scripts" / "_mypy_worker_timeout"
)

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
    ``os.environ``) widens the named-pipe worker timeouts at startup.
    """
    env = dict(os.environ if base is None else base)
    sitecustomize_dir = str(_MYPY_TIMEOUT_SITECUSTOMIZE_DIR)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        sitecustomize_dir + os.pathsep + existing if existing else sitecustomize_dir
    )
    return env


# mypy exit code for an internal error / crash (distinct from 1, real type
# errors). A ``--num-workers`` run can crash with this on Windows when a
# worker's IPC pipe breaks (``WinError 233``) even on type-clean code -- a mypy
# multiprocessing defect, not a type error -- so we retry such a crash
# single-process, whose result is authoritative.
_MYPY_INTERNAL_ERROR: Final[int] = 2


def _invoke_mypy(
    paths: list[str],
    *,
    env: dict[str, str] | None = None,
    extra: list[str] | None = None,
) -> int:
    """Run mypy over *paths*, retrying single-process on an internal-error crash.

    The first pass uses ``--num-workers`` (matching CI) with the worker-timeout
    sitecustomize wired via :func:`_mypy_env`, so a slow worker widens its IPC
    timeout rather than crashing. If it still exits with the internal-error code
    (a crash, never a type-error result), it is retried without ``--num-workers``
    so a Windows worker-IPC crash cannot block a type-clean push; a real type
    error (exit 1) is returned as-is, never masked.
    """
    extra = extra or []
    run_env = _mypy_env(env)
    base = [sys.executable, "-m", "mypy"]
    first = subprocess.run(
        [*base, *_MYPY_WORKERS, *extra, *paths],
        cwd=_REPO_ROOT,
        check=False,
        env=run_env,
    )
    if first.returncode != _MYPY_INTERNAL_ERROR:
        return first.returncode
    print(
        "mypy crashed under --num-workers (exit 2); retrying single-process",
        file=sys.stderr,
    )
    retry = subprocess.run(
        [*base, *extra, *paths],
        cwd=_REPO_ROOT,
        check=False,
        env=run_env,
    )
    return retry.returncode


def _run_mypy(paths: list[str]) -> int:
    """Run mypy with the given paths."""
    return _invoke_mypy(paths)


def _run_scripts_mypy() -> int:
    """Type-check ``scripts/`` with the flags its flat layout needs.

    ``scripts/`` is a flat directory whose modules clash on bare vs
    ``scripts.`` package names, so it needs ``MYPYPATH`` rooted at the
    repo plus ``--explicit-package-bases`` to resolve to canonical
    package names. Mirrors the second invocation in the CI type-check job.
    """
    env = {**os.environ, "MYPYPATH": str(_REPO_ROOT)}
    return _invoke_mypy(["scripts/"], env=env, extra=["--explicit-package-bases"])


def _run_full() -> int:
    """Run mypy across the whole tree, including the ``scripts/`` pass."""
    return max(_run_mypy(list(_FULL_SCOPE)), _run_scripts_mypy())


def main() -> int:
    """Entry point."""
    try:
        base = _merge_base()
    except _GitError as exc:
        print(f"ERROR: {exc} -- running full mypy", file=sys.stderr)
        return _run_full()

    try:
        changed = _changed_files(base)
    except _GitError as exc:
        print(f"ERROR: {exc} -- running full mypy", file=sys.stderr)
        return _run_full()

    # Filter to Python files only.
    py_changed = [f for f in changed if f.endswith(".py")]
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
