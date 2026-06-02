# module-kind: service
"""Assembles the living-document body from facts and prose.

The assembler renders the deterministic :class:`ReducedRun` into a tuple
of typed :data:`DocBlock` instances and weaves the LLM's connective
:class:`NarrativeProse` between the section headings. It is a pure
function: same inputs, same blocks. Decisions and metrics are emitted
verbatim; the prose only introduces each section.
"""

from synthorg.docs_engine.models import (
    BulletListBlock,
    DecisionBlock,
    DocBlock,
    HeadingBlock,
    LinkBlock,
    MetricBlock,
    ProseBlock,
)
from synthorg.meta.chief_of_staff.narrative.constants import (
    SECTION_CONTRIBUTIONS,
    SECTION_DECISIONS,
    SECTION_OPEN_ITEMS,
    SECTION_OUTCOMES,
    SECTION_SOURCES,
    SECTION_SUMMARY,
)
from synthorg.meta.chief_of_staff.narrative.models import (
    NarrativeProse,
    ReducedDecision,
    ReducedRun,
)

_SECTION_LEVEL: int = 2
_BULLET_MAX: int = 1024
_ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto"})
_NO_DECISIONS = "No decisions were recorded for this run."
_NO_CONTRIBUTIONS = "No agent activity was recorded for this run."


def assemble_blocks(reduced: ReducedRun, prose: NarrativeProse) -> tuple[DocBlock, ...]:
    """Render the reduced run and prose into a typed block body.

    Args:
        reduced: The fact-only rollup.
        prose: The connective narration from the synthesiser.

    Returns:
        The ordered tuple of document blocks.
    """
    blocks: list[DocBlock] = [
        HeadingBlock(level=_SECTION_LEVEL, text=SECTION_SUMMARY),
        ProseBlock(text=prose.summary),
    ]
    blocks.extend(
        MetricBlock(name=metric.name, value=metric.value, unit=metric.unit)
        for metric in reduced.metrics
    )
    _append_decisions(blocks, reduced, prose)
    _append_contributions(blocks, reduced, prose)
    _append_outcomes(blocks, reduced, prose)
    _append_open_items(blocks, reduced)
    _append_sources(blocks, reduced)
    return tuple(blocks)


def _append_decisions(
    blocks: list[DocBlock], reduced: ReducedRun, prose: NarrativeProse
) -> None:
    """Append the decisions section (heading, narration, decision blocks)."""
    blocks.append(HeadingBlock(level=_SECTION_LEVEL, text=SECTION_DECISIONS))
    if prose.decisions is not None:
        blocks.append(ProseBlock(text=prose.decisions))
    if not reduced.decisions:
        blocks.append(ProseBlock(text=_NO_DECISIONS))
        return
    for decision in reduced.decisions:
        blocks.append(
            DecisionBlock(decision=decision.outcome, rationale=decision.rationale)
        )
        _append_decision_detail(blocks, decision)


def _append_decision_detail(blocks: list[DocBlock], decision: ReducedDecision) -> None:
    """Append the alternatives and per-decision source lines, if any."""
    if decision.alternatives:
        blocks.append(
            BulletListBlock(
                items=tuple(
                    _clip(f"Considered: {alt}") for alt in decision.alternatives
                )
            )
        )
    if decision.sources:
        blocks.append(
            BulletListBlock(
                items=tuple(
                    _clip(f"Source: {source.label}") for source in decision.sources
                )
            )
        )


def _append_contributions(
    blocks: list[DocBlock], reduced: ReducedRun, prose: NarrativeProse
) -> None:
    """Append the who-did-what section."""
    blocks.append(HeadingBlock(level=_SECTION_LEVEL, text=SECTION_CONTRIBUTIONS))
    if prose.contributions is not None:
        blocks.append(ProseBlock(text=prose.contributions))
    if not reduced.contributions:
        blocks.append(ProseBlock(text=_NO_CONTRIBUTIONS))
        return
    items = tuple(
        _clip(
            f"{c.agent_id}: {c.turn_count} turn(s), "
            f"cost {c.cost:.2f} {reduced.currency}"
            + (f", tools: {', '.join(c.tools)}" if c.tools else "")
        )
        for c in reduced.contributions
    )
    blocks.append(BulletListBlock(items=items))


def _append_outcomes(
    blocks: list[DocBlock], reduced: ReducedRun, prose: NarrativeProse
) -> None:
    """Append the outcomes section."""
    blocks.append(HeadingBlock(level=_SECTION_LEVEL, text=SECTION_OUTCOMES))
    if prose.outcomes is not None:
        blocks.append(ProseBlock(text=prose.outcomes))
    if reduced.outcomes:
        blocks.append(
            BulletListBlock(items=tuple(_clip(line) for line in reduced.outcomes))
        )


def _append_open_items(blocks: list[DocBlock], reduced: ReducedRun) -> None:
    """Append the open-items section when any item is still live."""
    if not reduced.open_items:
        return
    blocks.append(HeadingBlock(level=_SECTION_LEVEL, text=SECTION_OPEN_ITEMS))
    blocks.append(
        BulletListBlock(
            items=tuple(
                _clip(f"{item.kind}: {item.title} ({item.status})")
                for item in reduced.open_items
            )
        )
    )


def _append_sources(blocks: list[DocBlock], reduced: ReducedRun) -> None:
    """Append the Sources section of provenance links."""
    if not reduced.sources:
        return
    blocks.append(HeadingBlock(level=_SECTION_LEVEL, text=SECTION_SOURCES))
    blocks.extend(
        LinkBlock(label=source.label, url=_safe_url(source.url))
        for source in reduced.sources
    )


def _safe_url(url: str) -> str:
    """Coerce a disallowed-scheme URL to a relative anchor.

    The :class:`LinkBlock` validator rejects schemes outside http /
    https / mailto (stored-XSS guard). A citation's external reference
    might carry an unexpected scheme; rather than fail the whole
    narrative, render it as a non-navigable relative anchor.

    A protocol-relative URL (``//host/path``) has no scheme but the
    browser resolves it against the page protocol, so it is an
    open-redirect vector; it is coerced too.

    Leading whitespace is stripped before the scheme checks because
    browsers trim it from ``href`` attributes, so ``" //evil"`` and
    ``" javascript:..."`` would otherwise slip past a naive prefix test.

    Returns:
        ``url`` unchanged when its scheme is permitted (or it is a
        genuine relative path / fragment), or a sanitised relative
        anchor otherwise.
    """
    trimmed = url.strip()
    if trimmed.startswith("//"):
        return "#external-protocol-relative"
    scheme, sep, _ = trimmed.partition(":")
    if not sep or "/" in scheme or scheme.lower() in _ALLOWED_URL_SCHEMES:
        return url
    return f"#external-{scheme.lower()}"


def _clip(text: str) -> str:
    """Clip a bullet item to the bounded maximum.

    Returns:
        ``text`` truncated to :data:`_BULLET_MAX` characters.
    """
    return text[:_BULLET_MAX]
