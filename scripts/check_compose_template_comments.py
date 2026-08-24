#!/usr/bin/env python3
"""Pre-push / CI gate: keep developer rationale out of the generated compose.

The compose template renders the file an operator runs their stack from,
and it carries two comment forms that look alike and behave completely
differently:

* ``{{- /* ... */}}`` is a Go template comment. It stays in the repo and
  never reaches the generated file.
* ``#`` is a YAML comment. It is copied verbatim into the operator's
  ``compose.yml``.

The template's own header declares the split: rationale goes in the first
form, and the second is reserved for the few warnings that are worth
something to somebody editing a file they were told not to edit. Nothing
enforced it, and the shipping side drifted until the rule was the
exception. Of 20 comment lines in the generated default stack, 2 passed
it, and the largest block was a ten-line note naming three PRIVATE Python
constants an operator cannot open, ending with an instruction addressed
to a developer.

Rationale that ships costs more than the lines. The CLI diffs the
generated file on update and asks the operator to approve the change, so
a prose edit becomes a prompt: one such diff ran to twelve lines, ten of
them comment. Every one of those trains the operator to click through a
diff they cannot evaluate, and one carried a mid-paragraph broken line
wrap all the way to that prompt.

So every shipping block is declared below in FULL, with the audience it
serves. Matching is equality against the block's normalised body, never
containment: a bare ``anchor in body`` substring test let two things
through, both reproduced against the real template. Appending an
unrelated sentence to an already-declared block passed, because the
anchor was still in there somewhere; and a brand-new block that merely
reused a declared block's words passed for the same reason. Equality
also removes two ways the declaration itself could go wrong, since an
empty anchor is a substring of every block (which silently disables the
check for the whole file) and an anchor that is a substring of another
lets one block satisfy two rows.

Normalisation strips the marker and collapses whitespace, so REFLOWING a
declared warning is free while changing its WORDS is a decision. That is
the split worth enforcing: rewrapping a paragraph is not a new claim on
the operator's attention; different words are.

A block with no declaration fails, and a declaration matching no block
fails too, since an allowance that outlives its comment is the one the
next comment inherits without anybody deciding.

``docker/compose.yml`` is deliberately out of scope, but NOT because
operators never read it: ``docs/user_guide.md`` and
``docs/guides/deployment.md`` both document running it directly, so they
do. It is out of scope because the two mechanisms this gate relies on are
absent there. It is hand-maintained rather than rendered, so there is no
second comment form to move rationale into; and nothing diffs it against
a freshly generated copy, so no prose edit turns into an approval prompt.
Drift there is caught by review, under the WHY-only comment rule. One
narrow axis is machine-checked: ``cli/internal/verify/compose_sync_test.go``
holds its postgres and nats image pins to the CLI's own constants.

There is deliberately no per-line opt-out. A genuine exception is a new
row below, which is where the claim "this reaches somebody who can act on
it" gets written down.

Usage::

    python scripts/check_compose_template_comments.py
    python scripts/check_compose_template_comments.py --repo-root .

Exit codes:
    0 -- every shipping comment is declared and within the cap.
    1 -- an undeclared block, an oversized block, or a stale allowance.
    2 -- configuration error (bad ``--repo-root``, an unreadable source,
         or a template path that cannot be derived).
"""

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final, NamedTuple

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import (  # type: ignore[import-not-found]
        GateSourceError,
        read_source,
    )
else:
    from scripts._gate_source import GateSourceError, read_source

# The Go file whose //go:embed directive names the template. Deriving the
# path from the embed rather than repeating it here means a rename that
# repoints Go while leaving a stale copy behind fails loudly instead of
# leaving this gate scanning an orphan and reporting success.
_GENERATE_GO_REL: Final[str] = "cli/internal/compose/generate.go"
_EMBED_RE: Final[re.Pattern[str]] = re.compile(r"^//go:embed\s+(\S+)\s*$", re.MULTILINE)

# Long enough for a warning, short enough that rationale cannot hide as
# one. Every declared block below fits; the ten-line shutdown note that
# prompted this gate does not.
_MAX_BLOCK_LINES: Final[int] = 5


class AllowedBlock(NamedTuple):
    """One sanctioned shipping comment block.

    Attributes:
        text: The block's complete body, normalised the way
            ``CommentBlock.body`` normalises what it finds. Compared by
            equality, so rewrapping the comment is free and rewording it
            requires editing this row.
        audience: Who the comment is for and what they can do about it.
            Printed when the declaration goes stale, because that is the
            moment somebody has to judge whether it still applies.
    """

    text: str
    audience: str


# Each row is a claim that the block reaches somebody who can act on it:
# an operator editing the file anyway, or one deciding whether to.
_ALLOWED_BLOCKS: Final[tuple[AllowedBlock, ...]] = (
    AllowedBlock(
        text=(
            "Generated by SynthOrg CLI {{.CLIVersion}} "
            "Do not edit manually -- run 'synthorg init' to regenerate."
        ),
        audience=(
            "anyone opening the file, who otherwise has no way to know "
            "their edits are overwritten by the next 'synthorg init'"
        ),
    ),
    AllowedBlock(
        text=(
            "WARNING: root-equivalent access to the host Docker daemon, needed "
            "so the backend can spawn sandbox containers. The hardening below "
            "does not contain socket-level privilege."
        ),
        audience=(
            "an operator weighing the sandbox against handing the "
            "backend a socket the rest of the hardening does not contain"
        ),
    ),
    AllowedBlock(
        text=(
            "The local Postgres image runs without TLS on the internal docker "
            "network. Production wants verify-full plus certificate material."
        ),
        audience=(
            "an operator taking this stack to production, where the "
            "shipped value is a weakened posture rather than a default"
        ),
    ),
    AllowedBlock(
        text="Rotating this invalidates every outstanding pagination cursor.",
        audience="an operator about to rotate the value on the line below",
    ),
    AllowedBlock(
        text=(
            "Permanent for the life of the install: rotation is not supported, "
            "and a changed key makes every stored connection secret unreadable."
        ),
        audience=(
            "an operator about to rotate a key whose loss makes every "
            "stored connection secret unreadable"
        ),
    ),
    AllowedBlock(
        text=(
            "Host-specific: the GID owning {{.DockerSock}}, so the non-root backend "
            "user can use the socket without relaxing host permissions. Re-run "
            "'synthorg init' when moving to another host."
        ),
        audience=(
            "an operator moving the install to another host, where the "
            "value below is stale and the backend loses the socket"
        ),
    ),
    AllowedBlock(
        text=(
            "The uid/gid on each mount is load-bearing: a tmpfs mounts root-owned "
            "by default, which Caddy (running as 65532) cannot write, so it falls "
            "back to read-only mode."
        ),
        audience=(
            "an operator debugging a Caddy write failure, who would "
            "otherwise reach for 'user: root' or drop read_only and undo "
            "the hardening this service is built on"
        ),
    ),
    AllowedBlock(
        text=(
            "Uncomment to expose NATS monitoring (/varz, /healthz, server stats) "
            "to the host. It is UNAUTHENTICATED; /api/v1/readyz is the supported "
            'health path. - "8222:8222"'
        ),
        audience=(
            "an operator debugging the bus, who needs to know the port "
            "is unauthenticated before uncommenting the line"
        ),
    ),
)

# Go template comments in every spelling: '{{/* */}}', '{{- /* */}}',
# and either with a trailing '-}}'. Body is non-greedy so two adjacent
# blocks do not collapse into one match.
_TEMPLATE_COMMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"\{\{-?\s*/\*.*?\*/\s*-?\}\}", re.DOTALL
)

# A single- or double-quoted YAML scalar. Blanked before looking for a
# comment marker so a '#' inside a value is not read as one.
_QUOTED_RE: Final[re.Pattern[str]] = re.compile(r"'[^']*'|\"[^\"]*\"")

# A YAML comment opens where '#' starts the line or follows whitespace.
# Anchoring on '^\s*#' alone made a trailing comment invisible rather
# than undeclared, so it shipped with no check of any kind.
_COMMENT_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|\s)#")


class DeclarationError(Exception):
    """A row in ``_ALLOWED_BLOCKS`` the matching loop cannot judge.

    Distinct from ``GateSourceError``, which covers an unreadable source
    file; this is the gate's own literal being wrong.
    """


class CommentBlock(NamedTuple):
    """A run of shipping comment lines that share a declaration.

    Attributes:
        start_line: 1-indexed line of the block's first comment.
        bodies: Each line's comment text, marker already stripped.
    """

    start_line: int
    bodies: tuple[str, ...]

    @property
    def body(self) -> str:
        """The block's text, whitespace collapsed to single spaces."""
        return " ".join(" ".join(line.split()) for line in self.bodies).strip()


def _reject_unjudgeable_declarations(blocks: tuple[AllowedBlock, ...]) -> None:
    """Reject a declaration set the matching loop cannot judge.

    Runs at import so a bad row fails immediately rather than quietly
    weakening the gate for every later run.

    Args:
        blocks: The declared blocks to validate.

    Raises:
        DeclarationError: A declaration is empty or duplicated.
    """
    if not blocks:
        message = "_ALLOWED_BLOCKS is empty, so this gate would refuse every comment"
        raise DeclarationError(message)
    seen: set[str] = set()
    for entry in blocks:
        if not entry.text.strip():
            message = (
                f"AllowedBlock has an empty text, which matches nothing and "
                f"leaves its audience unserved: audience={entry.audience!r}"
            )
            raise DeclarationError(message)
        if not entry.audience.strip():
            message = (
                f"AllowedBlock has no audience, so nothing records who the "
                f"comment is for: text={entry.text[:60]!r}"
            )
            raise DeclarationError(message)
        if entry.text in seen:
            message = f"AllowedBlock is declared twice: {entry.text[:60]!r}"
            raise DeclarationError(message)
        seen.add(entry.text)


_reject_unjudgeable_declarations(_ALLOWED_BLOCKS)


def resolve_template_rel(repo_root: Path) -> str:
    """Derive the template's path from the Go embed that renders it.

    Args:
        repo_root: Repository root the Go source is resolved against.

    Returns:
        The repository-relative path of the embedded template.

    Raises:
        GateSourceError: The Go source is unreadable or declares no
            embed, so this gate cannot know which file it checks.
    """
    source = read_source(repo_root / _GENERATE_GO_REL)
    match = _EMBED_RE.search(source)
    if match is None:
        message = (
            f"{_GENERATE_GO_REL}: no '//go:embed' directive found, so the "
            "compose template path cannot be derived and this gate does not "
            "know which file it is checking"
        )
        raise GateSourceError(message)
    return f"{Path(_GENERATE_GO_REL).parent.as_posix()}/{match.group(1)}"


def _blank_template_comments(source: str) -> str:
    """Replace Go template comments with blank lines of equal count.

    A ``#`` inside a template comment never ships, so it must not be read
    as one. Substituting newlines rather than deleting the region keeps
    every following line at its real number, which is what the violation
    messages point at.

    Args:
        source: The raw template text.

    Returns:
        The text with every template-comment region blanked.
    """

    def _blank(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    return _TEMPLATE_COMMENT_RE.sub(_blank, source)


def _split_comment(line: str) -> tuple[str, bool] | None:
    """Return a line's comment body and whether the line is only comment.

    Args:
        line: One source line, template comments already blanked.

    Returns:
        The comment text and whether the comment starts the line, or
        ``None`` when the line carries no comment.
    """
    masked = _QUOTED_RE.sub(lambda m: " " * len(m.group(0)), line)
    match = _COMMENT_RE.search(masked)
    if match is None:
        return None
    start = masked.index("#", match.start())
    return line[start + 1 :].strip(), not line[:start].strip()


def _comment_blocks(source: str) -> list[CommentBlock]:
    """Group the shipping comments into blocks.

    Consecutive whole-line comments form one block, because that is how a
    single warning is written. A comment trailing real content is its own
    block: it belongs to the line it annotates, not to whatever comment
    happens to sit above or below it.

    Args:
        source: Template text with template comments already blanked.

    Returns:
        Every shipping comment block, in file order.
    """
    blocks: list[CommentBlock] = []
    current: list[str] = []
    start = 0
    for number, line in enumerate(source.splitlines(), start=1):
        split = _split_comment(line)
        if split is not None and split[1]:
            if not current:
                start = number
            current.append(split[0])
            continue
        if current:
            blocks.append(CommentBlock(start, tuple(current)))
            current = []
        if split is not None:
            blocks.append(CommentBlock(number, (split[0],)))
    if current:
        blocks.append(CommentBlock(start, tuple(current)))
    return blocks


def _check(repo_root: Path) -> list[str]:
    """Hold every shipping comment to a declaration and the size cap.

    Args:
        repo_root: Repository root the template is resolved against.

    Returns:
        A list of violation messages (empty when the template is clean).

    Raises:
        GateSourceError: The template cannot be located or read, or has
            no shipping comment at all, which means the header vanished
            and the gate is reading the wrong file.
    """
    template_rel = resolve_template_rel(repo_root)
    source = read_source(repo_root / template_rel)
    blocks = _comment_blocks(_blank_template_comments(source))
    if not blocks:
        message = (
            f"{template_rel}: no '#' comment found at all, so the "
            "generated-file header is gone and this gate is checking nothing"
        )
        raise GateSourceError(message)

    declared = {entry.text: entry for entry in _ALLOWED_BLOCKS}
    violations: list[str] = []
    matched: set[str] = set()
    for block in blocks:
        body = block.body
        if body not in declared:
            violations.append(
                f"{template_rel}:{block.start_line}: undeclared '#' comment ships "
                f"to the operator's compose.yml -- move it into a "
                f"'{{{{- /* ... */}}}}' template comment, or declare it in "
                f"{Path(__file__).name} with the audience it serves: {body[:80]!r}"
            )
            continue
        matched.add(body)
        if len(block.bodies) > _MAX_BLOCK_LINES:
            violations.append(
                f"{template_rel}:{block.start_line}: shipping comment is "
                f"{len(block.bodies)} lines, over the {_MAX_BLOCK_LINES}-line cap "
                f"-- a warning this long has become rationale, which belongs in "
                f"a '{{{{- /* ... */}}}}' template comment"
            )

    violations.extend(
        f"{Path(__file__).name}: declared block {entry.text[:60]!r} matches "
        f"no comment in {template_rel} -- delete the row, or restore the "
        f"comment, which was for {entry.audience}"
        for entry in _ALLOWED_BLOCKS
        if entry.text not in matched
    )
    return violations


def main(argv: Sequence[str] | None = None) -> int:
    """Run the compose-template comment gate.

    Args:
        argv: Command-line arguments, or ``None`` to read ``sys.argv``.

    Returns:
        The process exit code (0 clean, 1 violations, 2 config error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        violations = _check(args.repo_root)
    except GateSourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if violations:
        print("Compose-template comment check failed:", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
