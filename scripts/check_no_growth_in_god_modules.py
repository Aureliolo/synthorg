#!/usr/bin/env python3
"""No-growth-in-god-modules gate.

A diff that touches any explicitly listed god-module (large central
files at the centre of the codebase) must NET-SHRINK that file. The
allowlist is a hard-coded constant in this script so adding to it is
a code change reviewable in a PR, not a CLI side-effect.

The gate runs at pre-commit / pre-push. For each allowlisted path it
compares the staged file's LOC (via ``git show :<path>``) to the
file's LOC at ``HEAD`` (via ``git show HEAD:<path>``); a strictly
larger staged LOC fails.

Newly created allowlisted files (no HEAD content) are permitted; the
expectation is they enter the allowlist alongside their introduction
PR, not as a way to admit new growth.

Usage::

    uv run python scripts/check_no_growth_in_god_modules.py
    uv run python scripts/check_no_growth_in_god_modules.py --list
"""

import argparse
import dataclasses
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final

_REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent

GOD_MODULE_ALLOWLIST: Final[tuple[str, ...]] = (
    "src/synthorg/api/app.py",
    "src/synthorg/api/state.py",
    "src/synthorg/api/auto_wire.py",
    "src/synthorg/api/lifecycle.py",
    "src/synthorg/api/lifecycle_builder.py",
    "src/synthorg/core/enums.py",
    "src/synthorg/observability/events/persistence.py",
)


@dataclasses.dataclass(frozen=True)
class Violation:
    """One allowlisted file that grew between HEAD and staged."""

    path: str
    head_loc: int
    staged_loc: int

    def render(self) -> str:
        """Format for stderr: ``<path>: <head_loc> -> <staged_loc> (+<delta>)``."""
        delta = self.staged_loc - self.head_loc
        return f"{self.path}: {self.head_loc} -> {self.staged_loc} (+{delta})"


def classify_change(
    *, path: str, head_loc: int | None, staged_loc: int | None
) -> Violation | None:
    """Decide whether a single allowlisted path's change is a violation.

    Args:
        path: Repo-relative POSIX path of the file.
        head_loc: LOC at HEAD (``None`` if the file did not exist there).
        staged_loc: LOC in the index (``None`` if not staged at all).

    Returns:
        A :class:`Violation` if staged LOC strictly exceeds HEAD LOC.
        ``None`` for net-shrink, no-change, file not staged, file newly
        created, or path outside the allowlist.
    """
    if path not in GOD_MODULE_ALLOWLIST:
        return None
    if staged_loc is None:
        return None
    if head_loc is None:
        return None
    if staged_loc <= head_loc:
        return None
    return Violation(path=path, head_loc=head_loc, staged_loc=staged_loc)


def classify_paths(
    *,
    paths: tuple[str, ...],
    read_staged_loc: Callable[[str], int | None],
    read_head_loc: Callable[[str], int | None],
) -> list[Violation]:
    """Classify every path; return the violations sorted by path."""
    violations: list[Violation] = []
    for path in paths:
        violation = classify_change(
            path=path,
            head_loc=read_head_loc(path),
            staged_loc=read_staged_loc(path),
        )
        if violation is not None:
            violations.append(violation)
    return sorted(violations, key=lambda v: v.path)


def _git_show(ref: str, path: str, *, repo_root: Path) -> str | None:
    """Return the content of *path* at *ref* inside *repo_root*, or ``None``.

    Runs ``git show`` with ``cwd=repo_root`` so the ``--repo-root``
    override actually selects the repository under test instead of
    silently falling back to the process CWD.

    Treats a missing ``git`` binary, the path not being present at the
    ref, and other OS errors as "no content" so the caller skips
    silently rather than crashing on infrastructure problems.
    """
    try:
        result = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            check=False,
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        print(f"WARNING: git show {ref}:{path} failed: {exc}", file=sys.stderr)
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _count_loc_from_text(text: str) -> int:
    """LOC count from raw text (mirrors :func:`_module_size_lib.count_loc`)."""
    return sum(
        1
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def _read_staged_loc(path: str, *, repo_root: Path) -> int | None:
    text = _git_show(":0", path, repo_root=repo_root)
    if text is None:
        return None
    return _count_loc_from_text(text)


def _read_head_loc(path: str, *, repo_root: Path) -> int | None:
    text = _git_show("HEAD", path, repo_root=repo_root)
    if text is None:
        return None
    return _count_loc_from_text(text)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the god-module allowlist (sorted lexically) and exit.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=_REPO_ROOT_DEFAULT,
        help="Override the repo root (default: scripts/.. relative to this file)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        ``0`` if no allowlisted file has net-grown; ``1`` otherwise.
    """
    args = _build_arg_parser().parse_args(argv)
    if args.list:
        for path in sorted(GOD_MODULE_ALLOWLIST):
            print(path)
        return 0
    repo_root: Path = args.repo_root.resolve()
    violations = classify_paths(
        paths=GOD_MODULE_ALLOWLIST,
        read_staged_loc=lambda p: _read_staged_loc(p, repo_root=repo_root),
        read_head_loc=lambda p: _read_head_loc(p, repo_root=repo_root),
    )
    if not violations:
        return 0
    print(
        "God-modules must net-shrink. These allowlisted files grew:",
        file=sys.stderr,
    )
    for violation in violations:
        print(f"  {violation.render()}", file=sys.stderr)
    print(
        "\nShrink the file or remove unrelated growth before pushing. "
        "Adding to the god-module allowlist requires a code change in "
        "scripts/check_no_growth_in_god_modules.py.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
