# module-kind: code
"""The output-style check both file-writing tools run before touching disk.

A file an agent writes is the thing the organisation ships, so this is the
enforcement point: the refusal comes back as the tool's own result and the
agent fixes it on its next turn, inside the session, rather than being
re-dispatched after it has ended.

Only a violation the write **introduces** blocks. Content that was already
there is not something the agent authored, and refusing an edit or an
overwrite over a character somebody else left behind gives it nothing it can
act on: it either mangles content it does not own or gives up. Both tools
therefore evaluate the candidate content whole (so a violation formed at the
seam between new text and its surroundings is caught) and subtract the
blocking findings the file already carried, so an already-violating file stays
editable while a write that adds a NEW violation is refused even when the file
violated a different rule already.

What identifies "the same violation" across the two evaluations is the rule
plus the TEXT OF THE LINE the match sits on, never the match alone. A literal
ban matches one character, so every occurrence of it in a file carries the
same snippet: keyed on that, a write that removes one occurrence while adding
another subtracts to nothing and the new one lands on disk, and the places
quoted back to the agent are the first ones in the file rather than the ones
it wrote. The line distinguishes them, with multiplicity, so both halves
follow: the count is right and the quoted places are the agent's own.

The line rather than the surrounding window, which reaches sixty characters
either side and so changes whenever anything NEAR a violation moves: appending
a line to a short file would re-present every violation in it as introduced.
The line survives an edit elsewhere, and matching on its text rather than its
number survives an insertion above it. Rewriting the line a violation sits on
does re-present it, which is the intended reading: an agent that re-authored
the line owns what the line now says.

One file is shared by ``write_file`` and ``edit_file`` because the rule is one
rule, and an overwrite that carries a character the agent never wrote is
refusable by exactly the same argument as an edit that does.
"""

from collections import Counter
from collections.abc import Sequence
from typing import Final

from synthorg.tools.base import ToolExecutionResult

#: Appended when the reporting cap swallowed the comparison. The cap bounds
#: findings per evaluation, so a file already over it reports the same
#: saturated set before and after, every subtraction is empty, and the
#: boundary silently stops guarding the one file that needs it most.
_SATURATED_NOTE: Final[str] = (
    " This file carries more style violations than one check reports, so which "
    "of them this write introduced cannot be established. Fix the places above "
    "and write again."
)


def _line_at(lines: Sequence[str], number: int) -> str:
    """Return the 1-based *number* line of *lines*.

    Returns:
        The line's text, or an empty string when the number falls outside it
        (a match on a trailing newline reports the line past the last).
    """
    index = number - 1
    return lines[index] if 0 <= index < len(lines) else ""


def guard_written_content(
    *,
    user_path: str,
    original: str,
    resulting: str,
) -> ToolExecutionResult | None:
    """Refuse *resulting* when it introduces a hard output-style violation.

    Code-channel throughout (reject, never auto-rewrite): a punctuation swap
    inside source could corrupt the program. An operator-sanctioned PATH
    exemption is what legitimately covers a file that must contain the
    literal.

    Args:
        user_path: The path as the agent named it, which the PATH exemption
            and the log both read.
        original: What the file held before this write. Empty for a create,
            which gives an empty baseline and so refuses every violation in
            the new content.
        resulting: The complete content the write would leave behind.

    Returns:
        An error result naming the places to fix, or ``None`` when nothing
        the write introduces blocks.
    """
    from synthorg.engine.output_style import (  # noqa: PLC0415
        OutputChannel,
        OutputContext,
        evaluate_output_policy,
    )
    from synthorg.engine.output_style.evaluator import MAX_FINDINGS  # noqa: PLC0415

    ctx = OutputContext(channel=OutputChannel.CODE_FILE, file_path=user_path or None)
    after = evaluate_output_policy(resulting, ctx)
    if after is None or not after.blocked:
        return None
    before = evaluate_output_policy(original, ctx)
    if len(after.findings) >= MAX_FINDINGS or (
        before is not None and len(before.findings) >= MAX_FINDINGS
    ):
        return ToolExecutionResult(
            content=after.summary + _SATURATED_NOTE,
            is_error=True,
        )
    original_lines = original.splitlines()
    lines = resulting.splitlines()
    baseline: Counter[tuple[str, str]] = Counter()
    if before is not None:
        baseline = Counter(
            (str(f.rule_id), _line_at(original_lines, f.line))
            for f in before.findings
            if f.blocks
        )
    introduced = (
        Counter(
            (str(f.rule_id), _line_at(lines, f.line))
            for f in after.findings
            if f.blocks
        )
        - baseline
    )
    if not introduced:
        return None
    # Reported through a verdict carrying only the introduced findings, so the
    # summary quotes the places the agent can actually fix rather than every
    # place the rule matched. The summary is derived from the findings, so a
    # copy over the filtered set cannot disagree with itself.
    remaining = Counter(introduced)
    kept = []
    for finding in after.findings:
        key = (str(finding.rule_id), _line_at(lines, finding.line))
        if finding.blocks and remaining[key] > 0:
            remaining[key] -= 1
            kept.append(finding)
    return ToolExecutionResult(
        content=after.model_copy(update={"findings": tuple(kept)}).summary,
        is_error=True,
    )


__all__ = ["guard_written_content"]
