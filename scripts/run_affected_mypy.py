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
Git command failures fall back to running full mypy on ``src/`` and ``tests/``.
"""

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Modules imported by nearly everything -- changes here mean "full mypy".
_BLAST_RADIUS_MODULES = frozenset({"core", "config", "observability"})

# Top-level source files that aren't in a module directory.
_TOP_LEVEL_SRC = frozenset({"__init__.py", "constants.py"})

# Minimum path depth for src/synthorg/<module> or tests/<kind>/<module>.
_MIN_MODULE_DEPTH = 3

# Test subdirectories that mypy should cover.
_TEST_KINDS = ("unit", "integration")

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


def _run_mypy(paths: list[str]) -> int:
    """Run mypy with the given paths."""
    cmd = [sys.executable, "-m", "mypy", "--num-workers=4", *paths]
    result = subprocess.run(cmd, cwd=_REPO_ROOT, check=False)
    return result.returncode


def main() -> int:
    """Entry point."""
    try:
        base = _merge_base()
    except _GitError as exc:
        print(f"ERROR: {exc} -- running full mypy", file=sys.stderr)
        return _run_mypy(["src/", "tests/"])

    try:
        changed = _changed_files(base)
    except _GitError as exc:
        print(f"ERROR: {exc} -- running full mypy", file=sys.stderr)
        return _run_mypy(["src/", "tests/"])

    # Filter to Python files only.
    py_changed = [f for f in changed if f.endswith(".py")]
    if not py_changed:
        print("No Python files changed -- skipping mypy.")
        return 0

    paths, run_all = _affected_mypy_paths(py_changed)

    if run_all:
        print("Foundational module or conftest changed -- running full mypy.")
        return _run_mypy(["src/", "tests/"])

    if not paths:
        print("Changed files don't map to any mypy targets -- skipping.")
        return 0

    print(f"Running mypy on: {', '.join(paths)}")
    return _run_mypy(paths)


if __name__ == "__main__":
    sys.exit(main())
