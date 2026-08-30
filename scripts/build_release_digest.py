#!/usr/bin/env python3
"""Build the changelog digest the release-notes model reads.

Reads a GitHub compare payload (``gh api repos/{repo}/compare/{base}...{head}``)
on stdin and writes a cleaned, capped digest to stdout.

The digest exists because a commit subject alone cannot support a summary: it
names the change without saying what it does. The full messages can, but this
repository squash-merges whole PR descriptions: a 183-commit release measured
~494k tokens of them, which overflows the input window of the model this
pipeline calls.

What survives is therefore chosen rather than truncated at an offset. Position
is a poor selector here: over that same release, half the bodies opened with
``Closes #N`` or a markdown heading, so a naive head-of-body slice buys length
without information. Structural noise is stripped first, then each commit is
capped, so the budget is spent on prose the model can actually use.

Every stripper here is deliberately CONSERVATIVE, because the two error
directions are not symmetric: keeping a line of noise costs a few tokens out of
a budget with room to spare, while dropping a line of substance loses the only
description of a change the release note will ever have. Each pattern therefore
demands the full shape of the thing it targets (a trailer needs its reference,
a severity header needs to be a bare header) rather than matching on a leading
word that ordinary prose also starts with.

Review chatter is dropped as a block, not a line: an opener such as
``Pre-reviewed by 18 agents, 35 findings addressed`` is followed by a bulleted
findings list, and keeping the list while dropping its opener would be worse
than keeping neither. The block ends at the first line that is not part of it,
so prose resuming straight after a findings list survives. Stripping runs
before heading removal because a heading is one of the things that ends a
block.

Dropping a block leaves a blank line behind rather than closing the gap. Two
lines that were never adjacent must not become one sentence: this text reaches
an LLM, and a removed span is otherwise a free way to weld unrelated prose
together.
"""

import json
import re
import sys
from typing import Final

# Per-commit budget. Measured against a 183-commit release: this keeps the
# digest near 25k tokens, which is both smaller and better-informed than any
# whole-paragraph slice, and leaves the model's window almost entirely free.
MAX_BODY_CHARS: Final[int] = 600

# Compare returns at most this many commits when called without pagination,
# which is how the workflow calls it; past that the digest is silently partial.
_COMPARE_COMMIT_CAP: Final[int] = 250

_ELLIPSIS: Final[str] = " [...]"

_HEADING = re.compile(r"^\s*#{1,6}\s")
_TABLE_ROW = re.compile(r"^\s*\|")
_FENCE = re.compile(r"^\s*```")
_BULLET = re.compile(r"^\s*[-*+]\s")
_INDENTED = re.compile(r"^\s+\S")

# A trailer needs its REFERENCE, not just its verb. "Fixes a race where two
# agents wrote the same file" opens with the same word as "Fixes #2862" and is
# exactly the sentence a release note wants, so matching the verb alone deleted
# the substance along with the trailer.
_ISSUE_REF = re.compile(
    r"^\s*(?:closes|fixes|resolves|refs|part of)\s+"
    r"(?:#\d+|[\w.-]+/[\w.-]+#\d+|https?://\S+)\s*(?:[.,;]\s*)?$",
    re.IGNORECASE,
)
_TRAILER = re.compile(
    r"^\s*(?:co-authored-by|signed-off-by|release-as|reviewed-by|"
    r"acked-by|tested-by):",
    re.IGNORECASE,
)
# Unrolled rather than the obvious ``<!--.*?-->``: a body carrying many
# unterminated ``<!--`` makes the lazy form rescan to end-of-string once per
# opener, which is quadratic in a commit message anyone who lands a commit
# gets to write. This form fails at the first ``-`` that does not open ``-->``,
# so a failed match advances instead of restarting.
_HTML_COMMENT = re.compile(r"<!--[^-]*(?:-(?!->)[^-]*)*-->", re.DOTALL)

# A link's text carries the reference a reader needs (``#2883``, a design page
# name); its URL is pure token cost and no summary ever quotes one.
# ``[`` is excluded from the label for the same reason as above: allowing it
# lets every ``[`` in a run start a scan that runs to the next ``]``.
_MD_LINK = re.compile(r"\[([^\][]+)\]\([^)]*\)")
_BARE_URL = re.compile(r"https?://\S+")

_BLANK_RUN = re.compile(r"\n{3,}")

# Openers observed across a full release: "Pre-reviewed by N agents",
# "Pre-PR reviewed by N agents", "Pre-reviewed locally", "Reviewed by N
# agents", "N agents reviewed the branch", "N local review agents",
# "Findings from the pre-PR review round".
#
# Every observed form carries a COUNT, and requiring it is what separates them
# from "Reviewed by the security team, who required rotating all API keys",
# which is release-note substance rather than process chatter.
_REVIEW_OPENER = re.compile(
    r"^\s*(?:\*\*)?(?:"
    r"pre-?(?:pr\s+)?reviewed\b"
    r"|reviewed\s+by\s+(?:\d+|an?\s+\d+)"
    r"|reviewed\s+locally\b"
    r"|\d+\s+(?:local\s+)?(?:review\s+)?agents?\s+reviewed\b"
    r"|\d+\s+local\s+review\s+agents\b"
    r"|findings?\s+from\s+the\s+pre-?pr\s+review\b"
    r")",
    re.IGNORECASE,
)

# Anchored to end-of-line, because a findings header is BARE. Without the
# anchor this prefix-matched "**Critical (2):** Fixes two data-loss bugs in the
# backup rotation job" and deleted the sentence with the label.
_SEVERITY_HEADER = re.compile(
    r"^\s*\*\*(?:critical|important|high|major|medium|moderate|minor|low|"
    r"nit|blocking|trivial|suggestions?)\b[^*]*\*\*\s*(?::\s*)?$",
    re.IGNORECASE,
)

# A dependency bump contributes nothing to a highlight or a tagline, so these
# keep their subject and lose their body wholesale. Matched on the bot-authored
# shapes rather than the bare word: "renovate" is also an ordinary verb, and a
# commit ABOUT the Renovate config is real work whose body must survive.
_NOISE_COMMIT = re.compile(
    r"renovate\[bot\]|dependabot\[bot\]|lock file maintenance"
    r"|^\s*chore\(deps[^)]*\)|^\s*build\(deps[^)]*\)",
    re.IGNORECASE | re.MULTILINE,
)
_NOISE_BODY_WINDOW: Final[int] = 400

# Collapses the runs of spaces left where an inline span was removed.
_SPACE_RUN = re.compile(r"[^\S\n]{2,}")

# The fence the workflow wraps this digest in before handing it to a model.
FENCE_TAG: Final[str] = "untrusted-changelog"

# Whitespace-tolerant, matching `engine/prompt_safety.py`'s own closing-tag
# escape: a lenient reader still treats `</tag >` as a close, so an exact
# literal match leaves the obvious variants working as a fence break.
_CLOSING_TAG = re.compile(rf"</\s*({re.escape(FENCE_TAG)})\s*>", re.IGNORECASE)


def _is_block_continuation(line: str) -> bool:
    """Report whether a line continues a review-findings block."""
    return bool(
        _BULLET.match(line) or _INDENTED.match(line) or _SEVERITY_HEADER.match(line)
    )


def _skip_review_block(lines: list[str], start: int) -> int:
    """Return the index just past the review block opening at ``start``.

    A block consumes only the lines that belong to it: its findings list and
    the blank lines inside it. It ends at a heading, or at any line that is
    ordinary prose rather than a continuation. Consuming every non-blank line
    until a blank-line gap instead swallowed a commit's real description
    whenever it followed a findings list directly.
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
            if probe >= len(lines) or not _is_block_continuation(lines[probe]):
                return probe
            index = probe
            continue
        if not _is_block_continuation(line):
            return index
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
            # A blank stands in for the removed span so the lines that flanked
            # it cannot read as one continuous sentence.
            kept.append("")
            continue
        kept.append(lines[index])
        index += 1
    return "\n".join(kept)


def _strip_structural_lines(body: str) -> str:
    """Remove headings, tables, fenced code, issue refs and trailers.

    Each removal leaves a blank line in place of the dropped span. Closing the
    gap instead would let a fenced block between two sentences disappear and
    weld them into one, which is a free way to compose an instruction out of
    text no reviewer ever saw as a single line.
    """
    kept: list[str] = []
    in_fence = False
    for line in body.split("\n"):
        if _FENCE.match(line):
            if in_fence:
                kept.append("")
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
            kept.append("")
            continue
        kept.append(line.rstrip())
    return "\n".join(kept)


def clean_body(body: str) -> str:
    """Reduce a commit body to the prose a summary can be written from."""
    # Inline spans become a space, never nothing: dropping the characters
    # outright fuses the words that flanked them into one new word.
    body = _HTML_COMMENT.sub(" ", body)
    body = strip_review_blocks(body)
    body = _strip_structural_lines(body)
    body = _MD_LINK.sub(r"\1", body)
    body = _BARE_URL.sub(" ", body)
    body = _SPACE_RUN.sub(" ", body)
    return _BLANK_RUN.sub("\n\n", body).strip()


def cap(text: str, limit: int) -> str:
    """Truncate at ``limit``, on a word boundary when the span holds one.

    A span with no space in it (a hash, a long token, a script that does not
    separate words with spaces) is cut at the limit instead.
    """
    if len(text) <= limit:
        return text
    head, _, _ = text[:limit].rpartition(" ")
    return (head or text[:limit]) + _ELLIPSIS


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
    """Build the digest from full commit messages, newest first.

    Reports the count of entries carrying no subject: a commit reaching the
    model as nothing is indistinguishable from a commit that was never in the
    range, and somebody asking why the summary omits their change deserves a
    better answer than silence.
    """
    entries: list[str] = []
    dropped = 0
    for message in messages:
        subject, body = split_message(message)
        if not subject:
            dropped += 1
            continue
        if is_noise_commit(subject, body):
            entries.append(subject)
            continue
        cleaned = cap(clean_body(body), limit)
        entries.append(f"{subject}\n{cleaned}" if cleaned else subject)
    if dropped:
        print(
            f"::warning::{dropped} commit(s) had no subject and were omitted",
            file=sys.stderr,
        )
    return "\n\n".join(entries)


def fence(digest: str) -> str:
    """Wrap the digest for a model, neutralising any closing tag inside it.

    Commit prose is human-written and reaches an LLM verbatim, so a body
    carrying its own closing tag could end the data region early and have what
    follows read as instruction. Escaping happens here rather than in the
    workflow because two shell copies of one regex is two regexes.
    """
    escaped = _CLOSING_TAG.sub(r"</\1_escaped>", digest)
    return f"<{FENCE_TAG}>\n{escaped}\n</{FENCE_TAG}>\n"


def main() -> int:
    """Read a compare payload on stdin, write the fenced digest to stdout."""
    payload = json.load(sys.stdin)
    # An error body is a JSON object too, and `.get("commits") or []` would
    # turn one into an empty digest that reads exactly like an empty range.
    if not isinstance(payload, dict) or "commits" not in payload:
        print(
            "::error::stdin is not a compare payload (no 'commits' key)",
            file=sys.stderr,
        )
        return 1
    commits = payload["commits"] or []
    total = payload.get("total_commits", len(commits))
    if total > len(commits) or len(commits) >= _COMPARE_COMMIT_CAP:
        print(
            f"::warning::compare returned {len(commits)} of {total} commits; "
            "digest is partial",
            file=sys.stderr,
        )
    messages = [c.get("commit", {}).get("message", "") for c in commits]
    # Newest first: the model reads the most recent and most quotable work
    # before anything else, and the tagline is drawn from the whole set.
    digest = build_digest(list(reversed(messages)))
    if not digest.strip():
        print("::warning::digest is empty; nothing to summarise", file=sys.stderr)
        return 0
    sys.stdout.write(fence(digest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
