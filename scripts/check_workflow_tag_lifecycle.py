#!/usr/bin/env python3
"""Pre-push gate: block tag-vs-downstream-checkout races.

A workflow that BOTH creates a tag via ``gh api .../git/refs`` POST AND
conditionally deletes that same tag via ``gh api -X DELETE
.../git/refs/tags/...`` (in the same step or across steps in the same
workflow file) reproduces the race documented in #1818:

1. Step 1 creates ``refs/tags/$DEV_TAG`` via ``gh api .../git/refs``.
   The ref-create fires ``push`` events to every ``tags: v*``-listening
   workflow (currently ``cli.yml`` and ``docker.yml``); those workflows
   begin running on hosted runners and ``actions/checkout`` the new
   tag within seconds.
2. Step 2 (or a later step in the same workflow) attempts a follow-on
   operation -- ``gh release create``, asset upload, etc.
3. On failure, a cleanup branch deletes the tag with ``gh api -X
   DELETE``.
4. Downstream tag-triggered runs already in flight 404 on
   ``actions/checkout`` while fetching the just-deleted ref. Result:
   false-positive red on ``main`` even when the squash content is
   clean.

The convention this gate enforces:

    A workflow MAY create tags. A workflow MAY delete tags. It MAY NOT
    do BOTH within the same workflow file -- the cleanup must happen in
    a separate workflow that does not also produce the original tag, so
    its delete cannot race a producer-side race window.

Reference fix: ``dev-release.yml`` after #1818 (release-create failure
preserves the orphan tag; the existing stale-pre-release sweeper +
``finalize-release.yml``'s stable-release sweep garbage-collect it
later in separate workflow runs that do NOT also mint tags).

This is a no-baseline gate: a NEW convention should pass clean from day
one. If this gate flags an existing workflow, fix the workflow -- do
NOT add a baseline. The MED severity audit pass for #1818 confirmed
the post-fix repo passes clean.

Usage::

    python scripts/check_workflow_tag_lifecycle.py <file>...   # pre-commit
    python scripts/check_workflow_tag_lifecycle.py --scan-all  # CI / manual
"""

import argparse
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS_ROOT = _REPO_ROOT / ".github" / "workflows"

# CREATE: ``gh api`` invocation hitting the ``/git/refs`` endpoint with
# an ``-f ref="refs/tags/...`` flag. ``gh api`` infers POST when a
# ``-f`` flag is present, so this captures the create-ref shape.
#
# Tolerance built into the pattern:
#
# - ``git/refs[^/\w]`` requires that ``git/refs`` be the leaf endpoint
#   (followed by quote / whitespace / EOL), NOT a path segment as in
#   ``git/refs/tags/...``. This excludes the DELETE shape, which always
#   has a trailing path component.
# - Backslash-newline continuations (``(?:[^\n]|\\\n)``) are tolerated
#   between every pair of tokens so a multi-line invocation cannot
#   bypass the gate just by wrapping. The 500 / 300 char windows cap
#   pairing distance so an unrelated downstream ``-f`` cannot bind to
#   a distant ``git/refs`` reference in the same run block.
# - Argument order is not fixed: ``-f ref=refs/tags/...`` may appear
#   either AFTER the endpoint (the common form) or BEFORE it. Both
#   orderings hit the same POST endpoint, so both are matched.
# - Anchored on ``refs/tags/`` so a heads-ref create (``refs/heads/...``)
#   does not falsely trip this gate.
_TAG_CREATE_RE = re.compile(
    r"gh\s+api(?:[^\n]|\\\n){0,500}?"
    r"(?:"
    r"git/refs[^/\w](?:[^\n]|\\\n){0,300}?-f\s+ref=[\"']?refs/tags/"
    r"|"
    r"-f\s+ref=[\"']?refs/tags/(?:[^\n]|\\\n){0,300}?git/refs[^/\w]"
    r")",
    re.MULTILINE,
)

# DELETE: any of the three ways a workflow can drop a tag the same job
# (or an earlier job in the same workflow) just minted:
#
# 1. ``gh api -X DELETE .../git/refs/tags/...`` (the empirical #1818
#    reproducer; the App-token authenticated call that fires the
#    downstream-cancelling ref-delete event).
# 2. ``gh api --method DELETE`` -- semantically identical to ``-X DELETE``.
# 3. ``gh release delete --cleanup-tag`` -- the GitHub CLI's release
#    command, which deletes both the release AND its tag in one call.
#
# Backslash-newline continuations are tolerated between every pair of
# tokens so a multi-line invocation cannot bypass the gate by wrapping;
# the 300 / 200 char windows cap pairing distance so unrelated calls
# in the same run block do not bind together.
#
# Per-line opt-out: a ``# lint-allow: workflow-tag-lifecycle -- <reason>``
# comment on the line containing the offending pattern (or any line the
# multi-line match spans) suppresses the report. Use this only when the
# delete provably targets tags whose downstream workflows have already
# completed (e.g. bulk-pruning of N-revisions-old dev tags), not the
# just-minted tag.
_TAG_DELETE_RE = re.compile(
    r"(?:"
    r"gh\s+api(?:[^\n]|\\\n){0,300}?"
    r"(?:-X|--method)\s+DELETE(?:[^\n]|\\\n){0,200}?"
    r"git/refs/tags/"
    r"|"
    r"gh\s+release\s+delete(?:[^\n]|\\\n){0,200}?--cleanup-tag"
    r")",
    re.MULTILINE,
)

# Strip full-line shell comments (``^[ \t]*#...``) before regex
# matching so a documented example in a ``run:`` block (``# example:
# gh api -X DELETE git/refs/tags/foo``) does not trip the gate. In-line
# ``#`` is left alone -- it is almost always part of a quoted string,
# not a comment. The leading character class is ``[ \t]*`` (NOT
# ``\s*``) on purpose: ``\s`` includes ``\n``, which would let the
# scrubber greedy-match a preceding blank line's newline along with
# the comment line's content, dropping a row from the file and
# silently shifting every later line number reported by the gate.
_SHELL_COMMENT_RE = re.compile(r"(?m)^[ \t]*#[^\n]*$")

# Per-line opt-out marker. Mandatory non-empty justification after
# ``--`` (whitespace-only is rejected). Detected on any of the line(s)
# the matched pattern spans; one opt-out is enough to suppress the
# report for that match.
_OPT_OUT_RE = re.compile(
    r"#\s*lint-allow:\s*workflow-tag-lifecycle\s*--\s*\S",
)

_STEERING_MESSAGE = (
    "Tag CREATE + conditional DELETE within a single workflow races "
    "downstream `tags: v*`-listening workflows on actions/checkout (#1818). "
    "Move the cleanup to a separate workflow that does not also produce "
    "the original tag, or leave orphan tags for the existing dev-pre-"
    "release sweeper to collect. Reference fix: dev-release.yml after "
    "#1818."
)


def _iter_workflow_files() -> Iterable[Path]:
    """Walk ``.github/workflows/`` for YAML files."""
    if not _WORKFLOWS_ROOT.exists():
        return
    for pattern in ("*.yml", "*.yaml"):
        yield from sorted(_WORKFLOWS_ROOT.rglob(pattern))


def _match_is_opted_out(
    scrubbed: str, raw_lines: list[str], match: re.Match[str]
) -> bool:
    """Return True if any line the match spans carries the opt-out marker.

    The opt-out check runs against the ORIGINAL source (``raw_lines``),
    not the scrubbed copy, so a per-line ``# lint-allow:`` comment is
    visible to the check even though the scrubber would normally strip
    full-line comments. The match's start/end are still computed from
    the scrubbed copy, but since the scrubber preserves newlines (it
    zeroes the text only) the 1-indexed line numbers align across both
    copies.
    """
    start_line = scrubbed[: match.start()].count("\n") + 1
    span_lines = scrubbed[match.start() : match.end()].count("\n") + 1
    end_line = start_line + span_lines - 1
    return any(
        _OPT_OUT_RE.search(raw_lines[line_no - 1])
        for line_no in range(start_line, end_line + 1)
        if 0 < line_no <= len(raw_lines)
    )


def _scan_file(path: Path) -> tuple[list[int], list[int]]:
    """Return ``(create_lines, delete_lines)`` 1-indexed line numbers.

    Caller flags the workflow when both lists are non-empty.

    ``_SHELL_COMMENT_RE`` substitution preserves newlines (it only
    zeros the comment text), so line numbers in the scrubbed source
    stay aligned with the original file.
    """
    raw = path.read_text(encoding="utf-8")
    raw_lines = raw.splitlines()
    scrubbed = _SHELL_COMMENT_RE.sub("", raw)
    creates: list[int] = []
    for match in _TAG_CREATE_RE.finditer(scrubbed):
        if _match_is_opted_out(scrubbed, raw_lines, match):
            continue
        line_no = scrubbed[: match.start()].count("\n") + 1
        creates.append(line_no)
    deletes: list[int] = []
    for match in _TAG_DELETE_RE.finditer(scrubbed):
        if _match_is_opted_out(scrubbed, raw_lines, match):
            continue
        line_no = scrubbed[: match.start()].count("\n") + 1
        deletes.append(line_no)
    return creates, deletes


def _scan_paths(paths: Iterable[Path]) -> int:
    """Scan each path; return shell exit code."""
    violations: list[tuple[Path, list[int], list[int]]] = []
    for path in paths:
        if not path.exists() or path.suffix not in (".yml", ".yaml"):
            continue
        creates, deletes = _scan_file(path)
        if creates and deletes:
            violations.append((path, creates, deletes))
    if not violations:
        return 0
    for path, creates, deletes in violations:
        resolved = path.resolve()
        try:
            rel = resolved.relative_to(_REPO_ROOT).as_posix()
        except ValueError:
            # Path is outside the repo root (e.g. an ad-hoc test
            # invocation against a tempfile). Fall back to the
            # absolute path so the reporter still produces a
            # navigable file:line citation.
            rel = resolved.as_posix()
        print(
            f"\n{rel}: tag CREATE and conditional DELETE in same workflow",
            file=sys.stderr,
        )
        for line in creates:
            print(f"  CREATE at {rel}:{line}", file=sys.stderr)
        for line in deletes:
            print(f"  DELETE at {rel}:{line}", file=sys.stderr)
    print(f"\n{_STEERING_MESSAGE}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Block tag-vs-downstream-checkout races in workflows.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files to check (pre-commit supplies these).",
    )
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="Scan every workflow file (CI / manual mode).",
    )
    args = parser.parse_args(argv)

    if args.scan_all:
        targets = list(_iter_workflow_files())
    elif args.paths:
        targets = [Path(p).resolve() for p in args.paths]
    else:
        # Default to scan-all when invoked without args, matching the
        # convention of the sibling check_workflow_shell_git_commits.py
        # gate.
        targets = list(_iter_workflow_files())
    return _scan_paths(targets)


if __name__ == "__main__":
    sys.exit(main())
