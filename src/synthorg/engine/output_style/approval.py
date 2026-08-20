# module-kind: code
"""Evaluate agent-authored text and hand back what may be kept.

Every enforcing boundary asks the same question in the same order: is the
policy wired, does anything block, did a rule rewrite this, otherwise keep what
was written. Five boundaries had five copies of that loop, each with its own
docstring saying it matched the others, which is how one of them comes to reject
where its siblings rewrite without anybody noticing.

The answer is a refusal STRING rather than a tool result, because the boundaries
do not share an error type: a file tool answers with an error result, a chat
send raises its own argument error, and a plan submission raises a decomposition
error. Each turns the refusal into its own; what they share is deciding what
the refusal says and which text survives.

Empty text is passed through untouched: a field the agent left blank is not
something it authored.
"""

from collections.abc import Sequence
from typing import NamedTuple

from synthorg.engine.output_style.exemptions import OutputContext
from synthorg.engine.output_style.interceptor import evaluate_output_policy


class TextApproval(NamedTuple):
    """What a boundary may keep, or why it may keep nothing.

    Attributes:
        refusal: The agent-facing reason when a hard rule blocked, else
            ``None``. Set means nothing was approved and ``texts`` is empty.
        texts: The approved values in the order supplied, each rewritten where
            an AUTO_REWRITE rule resolved it and otherwise unchanged.
    """

    refusal: str | None
    texts: tuple[str, ...]


def approve_texts(texts: Sequence[str], ctx: OutputContext) -> TextApproval:
    """Approve every string in *texts* for the boundary *ctx* describes.

    Stops at the first hard violation: the agent is handed one refusal naming
    the places in that field, and evaluating the rest would only add findings
    for text the same rework pass is about to change anyway.

    Args:
        texts: The agent-authored values, in the order the caller wants back.
        ctx: The boundary being written to, which decides both the channel's
            segmentation and any operator PATH exemption.

    Returns:
        The approval: a refusal, or the values the boundary may keep.
    """
    approved: list[str] = []
    for text in texts:
        if not text:
            approved.append(text)
            continue
        verdict = evaluate_output_policy(text, ctx)
        if verdict is None:
            approved.append(text)
            continue
        if verdict.blocked:
            return TextApproval(verdict.summary, ())
        approved.append(
            text if verdict.rewritten_text is None else verdict.rewritten_text
        )
    return TextApproval(None, tuple(approved))


__all__ = ["TextApproval", "approve_texts"]
