# module-kind: service
"""Deterministic reduction of raw run inputs into fact-only blocks.

Every value the reducer emits is sourced verbatim from the brain or the
flight recorder; the reducer performs no synthesis and makes no provider
call. Its output, :class:`ReducedRun`, is the trustworthy spine the
assembler renders and the synthesiser narrates around.
"""

from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.narrative.constants import (
    DECISION_TEXT_MAX,
    MAX_AGENTS_LISTED,
    MAX_SOURCES,
    MAX_TOOLS_PER_AGENT,
)
from synthorg.meta.chief_of_staff.narrative.models import (
    AgentContribution,
    OpenItem,
    ReducedDecision,
    ReducedRun,
    RunMetric,
    RunNarrativeInputs,
    SourceRef,
)
from synthorg.project_brain.models import (
    BrainEntry,
    Citation,
    CitationKind,
    DecisionPayload,
)

_LABEL_MAX: int = 512


def reduce_run(inputs: RunNarrativeInputs) -> ReducedRun:
    """Reduce gathered inputs into the fact-only :class:`ReducedRun`.

    Args:
        inputs: The raw material the reader gathered.

    Returns:
        The deterministic rollup the assembler will render.
    """
    decisions = tuple(_reduce_decision(entry) for entry in inputs.decisions)
    contributions = tuple(
        AgentContribution(
            agent_id=tally.agent_id,
            turn_count=tally.turn_count,
            cost=tally.cost,
            tools=tally.tools[:MAX_TOOLS_PER_AGENT],
        )
        for tally in inputs.agent_turns[:MAX_AGENTS_LISTED]
    )
    open_items = tuple(
        OpenItem(
            kind=summary.entry_kind,
            title=NotBlankStr(_clip(summary.title)),
            status=summary.status,
        )
        for summary in inputs.open_items
    )
    return ReducedRun(
        project_id=inputs.project_id,
        task_id=inputs.task_id,
        execution_id=inputs.execution_id,
        brief_title=inputs.brief_title,
        final_status=inputs.final_status,
        currency=inputs.currency,
        metrics=_metrics(
            inputs, decision_count=len(decisions), open_count=len(open_items)
        ),
        decisions=decisions,
        contributions=contributions,
        outcomes=_outcomes(
            inputs,
            agent_count=len(inputs.agent_turns),
            decision_count=len(decisions),
            open_count=len(open_items),
        ),
        open_items=open_items,
        sources=_sources(inputs),
    )


def _reduce_decision(entry: BrainEntry) -> ReducedDecision:
    """Shape one decision entry for verbatim rendering.

    Returns:
        The reduced decision, with its citations resolved to sources.
    """
    outcome: str = entry.title
    alternatives: tuple[str, ...] = ()
    payload = entry.payload
    if isinstance(payload, DecisionPayload):
        outcome = payload.decision_outcome
        alternatives = payload.alternatives
    # A brain rationale runs to 8192 chars but a DecisionBlock bounds its
    # text at 4096; clip both fields here so a rich-rationale decision
    # cannot raise on block construction and silently drop the narrative.
    return ReducedDecision(
        title=_clip(entry.title),
        outcome=_clip_decision_text(outcome),
        rationale=_clip_decision_text(entry.rationale),
        alternatives=tuple(_clip(alt) for alt in alternatives),
        sources=tuple(_citation_to_source(c) for c in entry.citations),
    )


def _metrics(
    inputs: RunNarrativeInputs,
    *,
    decision_count: int,
    open_count: int,
) -> tuple[RunMetric, ...]:
    """Build the run-metric blocks from aggregate facts.

    Returns:
        The ordered metric tuple.
    """
    return (
        RunMetric(name="Final status", value=NotBlankStr(inputs.final_status.value)),
        RunMetric(name="Turns", value=NotBlankStr(str(inputs.total_turns))),
        RunMetric(name="Agents", value=NotBlankStr(str(len(inputs.agent_turns)))),
        RunMetric(
            name="Total cost",
            value=NotBlankStr(f"{inputs.total_cost:.2f}"),
            unit=inputs.currency,
        ),
        RunMetric(name="Decisions", value=NotBlankStr(str(decision_count))),
        RunMetric(name="Open items", value=NotBlankStr(str(open_count))),
    )


def _outcomes(
    inputs: RunNarrativeInputs,
    *,
    agent_count: int,
    decision_count: int,
    open_count: int,
) -> tuple[str, ...]:
    """Build the outcome bullet lines from aggregate facts.

    Returns:
        The ordered outcome lines.
    """
    return (
        _clip(f"Final status: {inputs.final_status.value}"),
        _clip(f"{inputs.total_turns} turns across {agent_count} agent(s)"),
        _clip(f"Total cost: {inputs.total_cost:.2f} {inputs.currency}"),
        _clip(
            f"{decision_count} decision(s) recorded, "
            f"{open_count} open item(s) remaining"
        ),
    )


def _sources(inputs: RunNarrativeInputs) -> tuple[SourceRef, ...]:
    """Consolidate provenance across decisions and the brief, deduped.

    Returns:
        The ordered, deduplicated source references (bounded).
    """
    ordered: list[SourceRef] = [
        SourceRef(
            label=_clip(f"Brief task {inputs.task_id}"),
            url=_clip(f"#task-{inputs.task_id}"),
            kind=NotBlankStr("task"),
        )
    ]
    for entry in inputs.decisions:
        ordered.extend(_citation_to_source(c) for c in entry.citations)
    seen: set[str] = set()
    deduped: list[SourceRef] = []
    for source in ordered:
        if source.url in seen:
            continue
        seen.add(source.url)
        deduped.append(source)
    return tuple(deduped[:MAX_SOURCES])


def _citation_to_source(citation: Citation) -> SourceRef:
    """Map a brain citation to a renderable source reference.

    Returns:
        The :class:`SourceRef` for the citation.
    """
    ref = citation.source_ref
    locator = f" ({citation.locator})" if citation.locator else ""
    if citation.source_kind is CitationKind.EXTERNAL_URL:
        return SourceRef(
            label=_clip(f"{ref}{locator}"),
            url=_clip(ref),
            kind=NotBlankStr("external_url"),
        )
    prefix, label_word = _INTERNAL_KIND_RENDER[citation.source_kind]
    return SourceRef(
        label=_clip(f"{label_word} {ref}{locator}"),
        url=_clip(f"#{prefix}-{ref}"),
        kind=NotBlankStr(citation.source_kind.value),
    )


_INTERNAL_KIND_RENDER: dict[CitationKind, tuple[str, str]] = {
    CitationKind.TASK: ("task", "Task"),
    CitationKind.DOC_SLUG: ("doc", "Doc"),
    CitationKind.KNOWLEDGE_SOURCE: ("knowledge", "Knowledge source"),
    CitationKind.ENTRY: ("brain-entry", "Brain entry"),
}


def _clip(text: str) -> str:
    """Clip a label to the bounded-label maximum.

    Returns:
        ``text`` truncated to :data:`_LABEL_MAX` characters.
    """
    return text[:_LABEL_MAX]


def _clip_decision_text(text: str) -> str:
    """Clip decision outcome / rationale to the DecisionBlock bound.

    Returns:
        ``text`` truncated to :data:`DECISION_TEXT_MAX` characters.
    """
    return text[:DECISION_TEXT_MAX]
