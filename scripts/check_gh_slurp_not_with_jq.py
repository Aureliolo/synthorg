#!/usr/bin/env python3
r"""Pre-commit gate: ``gh api --slurp`` must not carry ``--jq``/``--template``.

``gh`` refuses the combination outright::

    the `--slurp` option is not supported with `--jq` or `--template`

It exits 1 *before making the request*, so the invocation never runs. That
matters more than an ordinary typo because the two flags are reached for
together for a good reason: ``--paginate --jq`` applies the filter once per
page, which makes ``first`` mean first-of-page, and ``--slurp`` is the
documented fix for exactly that. Writing both is the natural next step and
is the one thing that cannot work.

Neither actionlint nor shellcheck sees this: it is a ``gh`` CLI semantic,
not shell syntax. It shipped in three ``release-cut.yml`` call sites and
was caught only when a release ran, published no Highlights, and opened
the failure tracker.

The correct form pipes the slurped array into its own ``jq``::

    gh api "repos/$REPO/issues/$N/comments" --paginate --slurp \
      | jq -r "$SELECT | first | .id // empty"

Detection spans continuation lines, because every real occurrence is
written across several: the flags are joined with backslash-newline and a
line-at-a-time scan sees neither flag beside the other.

No baseline: an invocation that cannot execute is never worth preserving.

Usage::

    python scripts/check_gh_slurp_not_with_jq.py <file>...   # pre-commit
    python scripts/check_gh_slurp_not_with_jq.py --scan-all  # CI
"""

import argparse
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GITHUB_ROOT = _REPO_ROOT / ".github"

# Continuations are joined before matching, so `--slurp` on one line and
# `--jq` on the next are seen together; without that the gate is blind to
# every real case, since all of them are written across several lines.
_GH_API_CALL = re.compile(r"\bgh(?:_retry\s+gh)?\s+api\b")
_SLURP = re.compile(r"(?<![\w-])--slurp(?![\w-])")
_FILTER = re.compile(r"(?<![\w-])(?:--jq|-q|--template|-t)(?![\w-])")

_STEERING_MESSAGE = (
    "`gh api --slurp` cannot be combined with `--jq` / `--template`; gh "
    "exits 1 before making the request. Pipe the slurped array into its "
    "own jq instead:\n"
    '    gh api "<endpoint>" --paginate --slurp \\\n'
    '      | jq -r "$SELECT | first | .id // empty"'
)


class _UnreadableFileError(RuntimeError):
    """Raised when a scanned file cannot be decoded as UTF-8.

    Promoted to a violation by the callers so the gate never fails open on
    a file it could not inspect.
    """


def _iter_workflow_files() -> Iterable[Path]:
    """Walk ``.github/`` for YAML files (workflows, composites, configs)."""
    if not _GITHUB_ROOT.exists():
        return
    for pattern in ("*.yml", "*.yaml"):
        yield from sorted(_GITHUB_ROOT.rglob(pattern))


def _rel(path: Path) -> str:
    """Repo-relative POSIX path for stable error output."""
    try:
        return path.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _scan_file(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, statement)`` for every offending ``gh api`` call.

    The line number is the one the statement STARTS on, which is where the
    reader edits, rather than wherever the offending flag happens to land.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        msg = f"{_rel(path)}: could not read file: {type(exc).__name__}: {exc}"
        raise _UnreadableFileError(msg) from exc

    hits: list[tuple[int, str]] = []
    lines = source.splitlines()
    index = 0
    while index < len(lines):
        start = index
        statement = lines[index]
        # Absorb continuations so the flags are judged as one command.
        while statement.rstrip().endswith("\\") and index + 1 < len(lines):
            index += 1
            statement = f"{statement.rstrip()[:-1]} {lines[index].strip()}"
        if (
            _GH_API_CALL.search(statement)
            and _SLURP.search(statement)
            and _FILTER.search(statement)
        ):
            hits.append((start + 1, " ".join(statement.split())))
        index += 1
    return hits


def _report(violations: list[str]) -> int:
    """Print violations plus the steering message; return the exit code."""
    if not violations:
        return 0
    for line in violations:
        print(line)
    print(f"\n{_STEERING_MESSAGE}", file=sys.stderr)
    return 1


def _collect(paths: Iterable[Path]) -> list[str]:
    """Scan each path, promoting read failures to violations."""
    violations: list[str] = []
    for path in paths:
        try:
            hits = _scan_file(path)
        except _UnreadableFileError as exc:
            violations.append(str(exc))
            continue
        for lineno, statement in hits:
            violations.append(f"{_rel(path)}:{lineno}: {statement}")
    return violations


def cmd_scan_all() -> int:
    """Walk every YAML file under ``.github/`` and report every hit."""
    return _report(_collect(_iter_workflow_files()))


def cmd_scan_paths(paths: Iterable[str]) -> int:
    """Scan the provided files only -- pre-commit's canonical entry point."""
    selected: list[Path] = []
    for p in paths:
        path = Path(p).resolve()
        if not path.exists() or path.suffix not in (".yml", ".yaml"):
            continue
        if not path.is_relative_to(_GITHUB_ROOT):
            continue
        selected.append(path)
    return _report(_collect(selected))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Block `gh api --slurp` combined with `--jq`/`--template`.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files to check (pre-commit supplies these).",
    )
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="Scan every YAML file under .github/ (CI mode).",
    )
    args = parser.parse_args(argv)
    if args.scan_all:
        return cmd_scan_all()
    return cmd_scan_paths(args.paths)


if __name__ == "__main__":
    sys.exit(main())
