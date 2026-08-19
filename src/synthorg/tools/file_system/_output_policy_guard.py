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
blocking findings the file already carried, matched by rule id and offending
snippet with multiplicity, so an already-violating file stays editable while a
write that adds a NEW violation is refused even when the file violated a
different rule already.

Shared by ``write_file`` and ``edit_file`` because the rule is one rule. It
lived in ``edit_file`` alone, which left an overwrite refusable for a
character the agent had not written, and the artifact is exactly the path that
happens on.
"""

from collections import Counter

from synthorg.tools.base import ToolExecutionResult


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

    ctx = OutputContext(channel=OutputChannel.CODE_FILE, file_path=user_path or None)
    after = evaluate_output_policy(resulting, ctx)
    if after is None or not after.blocked:
        return None
    before = evaluate_output_policy(original, ctx)
    baseline: Counter[tuple[str, str]] = Counter()
    if before is not None:
        baseline = Counter(
            (f.rule_id, f.match_text) for f in before.findings if f.blocks
        )
    introduced = (
        Counter((f.rule_id, f.match_text) for f in after.findings if f.blocks)
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
        key = (finding.rule_id, finding.match_text)
        if finding.blocks and remaining[key] > 0:
            remaining[key] -= 1
            kept.append(finding)
    return ToolExecutionResult(
        content=after.model_copy(update={"findings": tuple(kept)}).summary,
        is_error=True,
    )


__all__ = ["guard_written_content"]
