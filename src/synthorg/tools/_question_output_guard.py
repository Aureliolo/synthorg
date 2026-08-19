# module-kind: code
"""Output-style guard for the human-facing text an agent's question carries.

A parked question is agent-authored prose rendered straight to a person in the
chat transcript, which makes it an agent-output boundary in exactly the sense
the output-style policy governs: the same class as an inter-agent message or a
PR body, not an internal data structure. It was not one before both question
tools defaulted on, so nothing guarded it.

Prose, not code: a question and its option writeups are addressed to a human,
so an AUTO_REWRITE rule's fixed text is applied rather than the whole thing
being rejected on a formatting nit. A hard-rule violation still blocks, and the
agent sees the summary and can reword.
"""

from synthorg.tools.base import ToolExecutionResult


def guard_question_text(*texts: str) -> tuple[ToolExecutionResult | None, list[str]]:
    """Enforce the output-style policy on every human-facing question string.

    Deferred import breaks the tools/engine cold-import cycle, matching the
    sibling forge and file-system guards.

    Args:
        *texts: The agent-authored strings, in the order the caller wants them
            back. An empty string is passed through untouched.

    Returns:
        An error result plus an empty list on a hard block, else ``None`` and
        the approved (possibly rewritten) strings in the order supplied.
    """
    from synthorg.engine.output_style import (  # noqa: PLC0415
        OutputChannel,
        OutputContext,
        approve_texts,
    )

    approval = approve_texts(texts, OutputContext(channel=OutputChannel.MESSAGE))
    if approval.refusal is not None:
        return ToolExecutionResult(content=approval.refusal, is_error=True), []
    return None, list(approval.texts)


__all__ = ["guard_question_text"]
