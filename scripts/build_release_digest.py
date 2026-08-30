#!/usr/bin/env python3
"""Build the changelog digest the release-notes model reads.

Reads a GitHub compare payload (``gh api repos/{repo}/compare/{base}...{head}``)
on stdin and writes a cleaned, capped digest to stdout.

The digest exists because a commit subject alone cannot support a summary: it
names the change without saying what it does. The full messages can, but this
repository squash-merges whole PR descriptions, so they run to ~500k tokens for
a single release, roughly twice any model's context window.

What survives is therefore chosen rather than truncated at an offset. Position
is a poor selector here: the opening paragraph of a body is usually ``Closes
#N`` or a markdown heading, so a naive head-of-body slice buys length without
information. Structural noise is stripped first, then each commit is capped, so
the budget is spent on prose the model can actually use.

Review chatter is dropped as a block, not a line: an opener such as
``Pre-reviewed by 18 agents, 35 findings addressed`` is followed by a bulleted
findings list, and keeping the list while dropping its opener would be worse
than keeping neither. Stripping runs before heading removal because a heading
is what terminates such a block.
"""

import json
import re
import sys
from typing import Final

# Per-commit budget. Measured against a 183-commit release: this keeps the
# digest near 25k tokens, which is both smaller and better-informed than any
# whole-paragraph slice, and leaves the model's window almost entirely free.
MAX_BODY_CHARS: Final[int] = 600

# GitHub's compare endpoint returns at most this many commits regardless of
# how many the range holds; past it the digest is silently partial.
_COMPARE_COMMIT_CAP: Final[int] = 250

_ELLIPSIS: Final[str] = " [...]"

_HEADING = re.compile(r"^\s*#{1,6}\s")
_TABLE_ROW = re.compile(r"^\s*\|")
_FENCE = re.compile(r"^\s*```")
_BULLET = re.compile(r"^\s*[-*+]\s")
_INDENTED = re.compile(r"^\s+\S")
_ISSUE_REF = re.compile(
    r"^\s*(?:closes|fixes|resolves|refs|part of)\b.*$", re.IGNORECASE
)
_TRAILER = re.compile(
    r"^\s*(?:co-authored-by|signed-off-by|release-as|reviewed-by|"
    r"acked-by|tested-by):",
    re.IGNORECASE,
)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

# A link's text carries the reference a reader needs (``#2883``, a design page
# name); its URL is pure token cost and no summary ever quotes one.
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_BARE_URL = re.compile(r"https?://\S+")

_BLANK_RUN = re.compile(r"\n{3,}")

# Openers observed across a full release: "Pre-reviewed by N agents",
# "Pre-PR reviewed by N agents", "Pre-reviewed locally", "Reviewed by N
# agents", "N agents reviewed the branch", "N local review agents",
# "Findings from the pre-PR review round".
_REVIEW_OPENER = re.compile(
    r"^\s*(?:\*\*)?(?:"
    r"pre-?(?:pr\s+)?reviewed\b"
    r"|reviewed\s+(?:by|locally)\b"
    r"|\d+\s+(?:local\s+)?(?:review\s+)?agents?\s+reviewed\b"
    r"|\d+\s+local\s+review\s+agents\b"
    r"|findings?\s+from\s+the\s+pre-?pr\s+review\b"
    r")",
    re.IGNORECASE,
)
_SEVERITY_HEADER = re.compile(
    r"^\s*\*\*(?:critical|important|high|major|medium|moderate|minor|low|"
    r"nit|blocking|trivial|suggestions?)\b[^*]*\*\*",
    re.IGNORECASE,
)

# A dependency bump contributes nothing to a highlight or a tagline, so these
# keep their subject and lose their body wholesale.
_NOISE_COMMIT = re.compile(r"renovate|dependabot|lock file maintenance", re.IGNORECASE)
_NOISE_BODY_WINDOW: Final[int] = 400


def _is_block_continuation(line: str) -> bool:
    """Report whether a line continues a review-findings block."""
    return bool(
        _BULLET.match(line) or _INDENTED.match(line) or _SEVERITY_HEADER.match(line)
    )


def _skip_review_block(lines: list[str], start: int) -> int:
    """Return the index just past the review block opening at ``start``.

    A block runs from its opener to the next heading, or to the first blank
    line whose next non-blank line resumes ordinary prose.
    """
    index = start + 1
    while index < len(lines):
        line = lines[index]
        if _HEADING.match(line):
            return index
        if not line.strip():
            probe = index + 1
            while probe < len(lines) and not lines[probe].strip():
                probe += 1
            if probe >= len(lines):
                return probe
            if not _is_block_continuation(lines[probe]):
                return probe
        index += 1
    return index


def strip_review_blocks(body: str) -> str:
    """Drop pre-PR review chatter, opener and findings list together."""
    lines = body.split("\n")
    kept: list[str] = []
    index = 0
    while index < len(lines):
        if _REVIEW_OPENER.match(lines[index]) or _SEVERITY_HEADER.match(lines[index]):
            index = _skip_review_block(lines, index)
            continue
        kept.append(lines[index])
        index += 1
    return "\n".join(kept)


def _strip_structural_lines(body: str) -> str:
    """Remove headings, tables, fenced code, issue refs and trailers."""
    kept: list[str] = []
    in_fence = False
    for line in body.split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if (
            _HEADING.match(line)
            or _TABLE_ROW.match(line)
            or _ISSUE_REF.match(line)
            or _TRAILER.match(line)
        ):
            continue
        kept.append(line.rstrip())
    return "\n".join(kept)


def clean_body(body: str) -> str:
    """Reduce a commit body to the prose a summary can be written from."""
    body = _HTML_COMMENT.sub("", body)
    body = strip_review_blocks(body)
    body = _strip_structural_lines(body)
    body = _MD_LINK.sub(r"\1", body)
    body = _BARE_URL.sub("", body)
    return _BLANK_RUN.sub("\n\n", body).strip()


def cap(text: str, limit: int) -> str:
    """Truncate on a word boundary, marking that content was dropped."""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + _ELLIPSIS


def is_noise_commit(subject: str, body: str) -> bool:
    """Report whether a commit is an automated dependency update."""
    return bool(
        _NOISE_COMMIT.search(subject) or _NOISE_COMMIT.search(body[:_NOISE_BODY_WINDOW])
    )


def split_message(message: str) -> tuple[str, str]:
    """Split a full commit message into its subject and body."""
    lines = message.split("\n")
    return lines[0].strip(), "\n".join(lines[1:])


def build_digest(messages: list[str], limit: int = MAX_BODY_CHARS) -> str:
    """Build the digest from full commit messages, newest first."""
    entries: list[str] = []
    for message in messages:
        subject, body = split_message(message)
        if not subject:
            continue
        if is_noise_commit(subject, body):
            entries.append(subject)
            continue
        cleaned = cap(clean_body(body), limit)
        entries.append(f"{subject}\n{cleaned}" if cleaned else subject)
    return "\n\n".join(entries)


def main() -> int:
    """Read a compare payload on stdin, write the digest to stdout."""
    payload = json.load(sys.stdin)
    commits = payload.get("commits") or []
    total = payload.get("total_commits", len(commits))
    if total > len(commits) or len(commits) >= _COMPARE_COMMIT_CAP:
        print(
            f"::warning::compare returned {len(commits)} of {total} commits; "
            "digest is partial",
            file=sys.stderr,
        )
    messages = [c.get("commit", {}).get("message", "") for c in commits]
    # Newest first, so a cap applied downstream drops the oldest work rather
    # than the release's most recent and most quotable changes.
    sys.stdout.write(build_digest(list(reversed(messages))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
