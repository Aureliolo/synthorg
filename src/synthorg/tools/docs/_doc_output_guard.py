# module-kind: code
"""Output-style guard for a living document an agent publishes.

A living document is something the organisation keeps: it lands in the project
wiki under the agent's name and people read it there. That makes it an
agent-output boundary in the same class as a PR body, and the guard runs here,
in-session, so a refusal comes back as the tool's own result and the agent
fixes it on its next turn rather than after the session has ended.

Fields are split by what they *are* rather than by which block carries them.
Prose (a heading, a paragraph, a bullet, a decision and its rationale, a metric
label, a link's visible text) is evaluated on a prose channel, so an
AUTO_REWRITE rule's fixed text is applied and the agent is not stopped over a
formatting nit. A literal (a code body, a metric's measured value, a URL) is
evaluated on a code channel, which the segmenter treats as one code span and
therefore rejects rather than rewrites: a punctuation swap inside a URL or a
measurement corrupts it, which is the ruling the segmenter already makes for a
fenced block inside a PR body.

``slug``, ``tags`` and ``related_task_ids`` are deliberately outside the
boundary. All three are keys rather than prose: the slug identifies the
document across revisions, the tags are its retrieval terms, and the task ids
are foreign keys. Rewriting any of them would repoint a reader's link or drop a
document out of its own search results.
"""

from typing import NamedTuple

from synthorg.docs_engine.models import (
    BulletListBlock,
    CodeBlock,
    DecisionBlock,
    DocBlock,
    HeadingBlock,
    LinkBlock,
    MetricBlock,
    ProseBlock,
)
from synthorg.tools.base import ToolExecutionResult


class DocOutputGuard(NamedTuple):
    """Outcome of guarding a living document's agent-authored text.

    ``error`` is set on a hard block, in which case ``title`` and ``body`` are
    the caller's originals and nothing must be written. Otherwise both carry
    the policy-approved values (rewritten where an AUTO_REWRITE rule fired,
    else unchanged).
    """

    error: ToolExecutionResult | None
    title: str
    body: tuple[DocBlock, ...]


class _FieldVerdict(NamedTuple):
    """One field's outcome: an error, or the approved text."""

    error: ToolExecutionResult | None
    text: str


def _approve(text: str, *, prose: bool, where: str) -> _FieldVerdict:
    """Evaluate one field's text on the channel its content warrants.

    Deferred import breaks the tools/engine cold-import cycle, matching the
    sibling forge, file-system and question guards.

    Args:
        text: The agent-authored value. An empty string is passed through.
        prose: Whether the value is prose a rewrite may safely touch.
        where: Human-readable location, prefixed to a refusal so the agent is
            told which part of the document to fix.

    Returns:
        An error plus an empty string on a hard block, else ``None`` and the
        approved text.
    """
    from synthorg.engine.output_style import (  # noqa: PLC0415
        OutputChannel,
        OutputContext,
        evaluate_output_policy,
    )

    if not text:
        return _FieldVerdict(None, text)
    channel = OutputChannel.DELIVERABLE if prose else OutputChannel.CODE_FILE
    verdict = evaluate_output_policy(text, OutputContext(channel=channel))
    if verdict is None:
        return _FieldVerdict(None, text)
    if verdict.blocked:
        return _FieldVerdict(
            ToolExecutionResult(content=f"{where}: {verdict.summary}", is_error=True),
            "",
        )
    if verdict.rewritten_text is not None:
        return _FieldVerdict(None, verdict.rewritten_text)
    return _FieldVerdict(None, text)


def _fields_of(block: DocBlock) -> tuple[tuple[str, str, bool], ...]:
    """Enumerate a block's agent-authored fields as (name, value, is_prose).

    Written as explicit narrowing rather than a name table so the compiler
    checks that every field named here exists on the block that carries it: a
    table of strings drifts silently the first time a block gains a field.
    A bullet list returns nothing, because rebuilding its tuple is its own
    branch in :func:`_guard_block`.

    Returns:
        Every field the policy governs, in the order a refusal should name.
    """
    if isinstance(block, HeadingBlock | ProseBlock):
        return (("text", block.text, True),)
    if isinstance(block, CodeBlock):
        return (("code", block.code, False),)
    if isinstance(block, DecisionBlock):
        return (
            ("decision", block.decision, True),
            ("rationale", block.rationale, True),
        )
    if isinstance(block, MetricBlock):
        unit = () if block.unit is None else (("unit", block.unit, True),)
        return (("name", block.name, True), ("value", block.value, False), *unit)
    if isinstance(block, LinkBlock):
        return (("label", block.label, True), ("url", block.url, False))
    return ()


def _guard_bullets(
    block: BulletListBlock, where: str
) -> tuple[ToolExecutionResult | None, DocBlock]:
    """Guard every entry of a bullet list, rebuilding it from the approved text.

    Returns:
        An error and the untouched block on a hard block, else ``None`` and the
        block carrying its approved items.
    """
    approved: list[str] = []
    for position, item in enumerate(block.items):
        verdict = _approve(item, prose=True, where=f"{where} bullet {position + 1}")
        if verdict.error is not None:
            return verdict.error, block
        approved.append(verdict.text)
    return None, block.model_copy(update={"items": tuple(approved)})


def _guard_block(
    block: DocBlock, index: int
) -> tuple[ToolExecutionResult | None, DocBlock]:
    """Guard one block's agent-authored fields.

    Returns:
        An error and the untouched block on a hard block, else ``None`` and the
        block carrying its approved field values.
    """
    where = f"block {index + 1} ({block.block_kind})"
    if isinstance(block, BulletListBlock):
        return _guard_bullets(block, where)
    updates: dict[str, str] = {}
    for name, value, prose in _fields_of(block):
        verdict = _approve(value, prose=prose, where=f"{where} {name}")
        if verdict.error is not None:
            return verdict.error, block
        updates[name] = verdict.text
    return None, block.model_copy(update=updates)


def guard_doc_output(*, title: str, body: tuple[DocBlock, ...]) -> DocOutputGuard:
    """Enforce the output-style policy on a living document before it is written.

    Args:
        title: The document title, the prose a reader sees first.
        body: The typed blocks the agent authored, in order.

    Returns:
        A guard carrying an ``error`` on a hard block, else the approved title
        and blocks.
    """
    title_verdict = _approve(title, prose=True, where="title")
    if title_verdict.error is not None:
        return DocOutputGuard(title_verdict.error, title, body)
    approved: list[DocBlock] = []
    for index, block in enumerate(body):
        error, guarded = _guard_block(block, index)
        if error is not None:
            return DocOutputGuard(error, title, body)
        approved.append(guarded)
    return DocOutputGuard(None, title_verdict.text, tuple(approved))


__all__ = ["DocOutputGuard", "guard_doc_output"]
