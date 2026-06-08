"""Unit tests for the run-narrative reducer."""

from datetime import UTC, datetime

import pytest

from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.models import DecisionBlock
from synthorg.meta.chief_of_staff.narrative.assembler import assemble_blocks
from synthorg.meta.chief_of_staff.narrative.constants import (
    DECISION_TEXT_MAX,
    MAX_TOOLS_PER_AGENT,
)
from synthorg.meta.chief_of_staff.narrative.models import (
    AgentTurnTally,
    NarrativeProse,
    RunNarrativeInputs,
)
from synthorg.meta.chief_of_staff.narrative.reducer import reduce_run
from synthorg.project_brain.models import (
    BrainEntry,
    BrainEntryKind,
    BrainEntryStatus,
    BrainSummary,
    Citation,
    CitationKind,
    DecisionPayload,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _decision_entry(
    *,
    citations: tuple[Citation, ...] = (),
    alternatives: tuple[str, ...] = (),
    rationale: str = "Auditability outweighs write amplification.",
) -> BrainEntry:
    return BrainEntry(
        entry_id=NotBlankStr("dec-1"),
        revision=1,
        project_id=NotBlankStr("proj-1"),
        entry_kind=BrainEntryKind.DECISION,
        title=NotBlankStr("Adopt event-sourced ledger"),
        rationale=NotBlankStr(rationale),
        status=BrainEntryStatus.ACCEPTED,
        author=NotBlankStr("agent-a"),
        recorded_at=_NOW,
        citations=citations,
        payload=DecisionPayload(
            decision_outcome=NotBlankStr("Event-sourced ledger"),
            alternatives=tuple(NotBlankStr(a) for a in alternatives),
        ),
    )


def _open_item() -> BrainSummary:
    return BrainSummary(
        project_id=NotBlankStr("proj-1"),
        entry_id=NotBlankStr("risk-1"),
        revision=1,
        entry_kind=BrainEntryKind.RISK,
        title=NotBlankStr("Latency under load"),
        status=BrainEntryStatus.ACTIVE,
        author=NotBlankStr("agent-b"),
        recorded_at=_NOW,
    )


def _inputs(
    *,
    decisions: tuple[BrainEntry, ...] = (),
    open_items: tuple[BrainSummary, ...] = (),
    agent_turns: tuple[AgentTurnTally, ...] = (),
) -> RunNarrativeInputs:
    return RunNarrativeInputs(
        project_id=NotBlankStr("proj-1"),
        task_id=NotBlankStr("task-1"),
        execution_id=NotBlankStr("exec-1"),
        brief_title=NotBlankStr("Ship checkout"),
        final_status=TaskStatus.COMPLETED,
        total_cost=3.5,
        total_turns=12,
        frame_count=12,
        decisions=decisions,
        open_items=open_items,
        agent_turns=agent_turns,
    )


class TestReduceRun:
    def test_decision_carries_outcome_rationale_alternatives(self) -> None:
        reduced = reduce_run(
            _inputs(
                decisions=(_decision_entry(alternatives=("CRUD ledger", "Append log")),)
            )
        )
        assert len(reduced.decisions) == 1
        decision = reduced.decisions[0]
        assert decision.outcome == "Event-sourced ledger"
        assert decision.rationale.startswith("Auditability")
        assert decision.alternatives == ("CRUD ledger", "Append log")

    def test_long_rationale_clipped_for_decision_block(self) -> None:
        # A brain rationale may run to 8192 chars, but a DecisionBlock
        # bounds its text at 4096. The reducer must clip so the block
        # cannot raise and silently drop the richest-rationale runs.
        long_rationale = "x" * (DECISION_TEXT_MAX + 1000)
        reduced = reduce_run(
            _inputs(decisions=(_decision_entry(rationale=long_rationale),))
        )
        assert len(reduced.decisions[0].rationale) == DECISION_TEXT_MAX
        # The clipped rationale must survive real block construction.
        blocks = assemble_blocks(reduced, NarrativeProse(summary="A clean run."))
        assert any(isinstance(b, DecisionBlock) for b in blocks)

    def test_cost_metric_carries_currency(self) -> None:
        reduced = reduce_run(_inputs())
        cost = next(m for m in reduced.metrics if m.name == "Total cost")
        # The default-currency inputs render the cost metric with a unit,
        # never a bare number (regional-defaults).
        assert cost.unit == "USD"
        assert reduced.currency == "USD"

    def test_decision_citations_become_sources(self) -> None:
        reduced = reduce_run(
            _inputs(
                decisions=(
                    _decision_entry(
                        citations=(
                            Citation(
                                source_ref=NotBlankStr("e7"),
                                source_kind=CitationKind.ENTRY,
                            ),
                        )
                    ),
                )
            )
        )
        urls = {s.url for s in reduced.decisions[0].sources}
        assert "#brain-entry-e7" in urls

    def test_external_url_citation_preserves_url(self) -> None:
        reduced = reduce_run(
            _inputs(
                decisions=(
                    _decision_entry(
                        citations=(
                            Citation(
                                source_ref=NotBlankStr("https://example.com/rfc"),
                                source_kind=CitationKind.EXTERNAL_URL,
                            ),
                        )
                    ),
                )
            )
        )
        source = reduced.decisions[0].sources[0]
        assert source.url == "https://example.com/rfc"
        assert source.kind == "external_url"

    def test_unmapped_citation_kind_renders_generic_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A CitationKind added after the render map was written must not
        # raise KeyError mid-narrative; it falls back to a generic prefix
        # and a humanised label. Simulate the future enum member by
        # emptying the map so an existing internal kind hits the fallback.
        import synthorg.meta.chief_of_staff.narrative.reducer as reducer_mod

        monkeypatch.setattr(reducer_mod, "_INTERNAL_KIND_RENDER", {})
        reduced = reduce_run(
            _inputs(
                decisions=(
                    _decision_entry(
                        citations=(
                            Citation(
                                source_ref=NotBlankStr("k9"),
                                source_kind=CitationKind.KNOWLEDGE_SOURCE,
                            ),
                        )
                    ),
                )
            )
        )
        source = reduced.decisions[0].sources[0]
        assert source.url == "#source-k9"
        assert source.label.startswith("Knowledge source k9")

    def test_sources_lead_with_brief_and_dedup(self) -> None:
        shared = Citation(
            source_ref=NotBlankStr("task-1"), source_kind=CitationKind.TASK
        )
        reduced = reduce_run(
            _inputs(decisions=(_decision_entry(citations=(shared, shared)),))
        )
        assert reduced.sources[0].url == "#task-task-1"
        urls = [s.url for s in reduced.sources]
        assert len(urls) == len(set(urls))

    def test_metrics_cover_status_turns_cost(self) -> None:
        reduced = reduce_run(_inputs())
        names = {m.name: m.value for m in reduced.metrics}
        assert names["Final status"] == "completed"
        assert names["Turns"] == "12"
        assert names["Total cost"] == "3.50"

    def test_contributions_clip_tools(self) -> None:
        many_tools = tuple(f"tool-{i}" for i in range(MAX_TOOLS_PER_AGENT + 5))
        reduced = reduce_run(
            _inputs(
                agent_turns=(
                    AgentTurnTally(
                        agent_id=NotBlankStr("agent-a"),
                        turn_count=20,
                        cost=2.0,
                        tools=many_tools,
                    ),
                )
            )
        )
        assert len(reduced.contributions[0].tools) == MAX_TOOLS_PER_AGENT

    def test_open_items_mapped(self) -> None:
        reduced = reduce_run(_inputs(open_items=(_open_item(),)))
        assert reduced.open_items[0].kind == "risk"
        assert reduced.open_items[0].status == "active"
        assert reduced.open_items[0].title == "Latency under load"

    def test_outcomes_lines_present(self) -> None:
        reduced = reduce_run(_inputs())
        assert any("Final status" in line for line in reduced.outcomes)
        assert any("turns across" in line for line in reduced.outcomes)
