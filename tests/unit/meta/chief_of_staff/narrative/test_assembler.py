"""Unit tests for the run-narrative assembler."""

import pytest

from synthorg.core.enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.models import (
    BulletListBlock,
    DecisionBlock,
    HeadingBlock,
    LinkBlock,
    MetricBlock,
    ProseBlock,
)
from synthorg.meta.chief_of_staff.narrative.assembler import (
    _safe_url,
    assemble_blocks,
)
from synthorg.meta.chief_of_staff.narrative.constants import (
    SECTION_OPEN_ITEMS,
    SECTION_SOURCES,
    SECTION_SUMMARY,
)
from synthorg.meta.chief_of_staff.narrative.models import (
    AgentContribution,
    NarrativeProse,
    OpenItem,
    ReducedDecision,
    ReducedRun,
    RunMetric,
    SourceRef,
)

pytestmark = pytest.mark.unit


def _decision() -> ReducedDecision:
    return ReducedDecision(
        title="Adopt ledger",
        outcome="Event-sourced ledger",
        rationale="Auditability wins.",
        alternatives=("CRUD ledger",),
        sources=(
            SourceRef(
                label="Brain entry e7", url="#brain-entry-e7", kind=NotBlankStr("entry")
            ),
        ),
    )


def _run(
    *,
    decisions: tuple[ReducedDecision, ...] = (),
    contributions: tuple[AgentContribution, ...] = (),
    open_items: tuple[OpenItem, ...] = (),
    sources: tuple[SourceRef, ...] = (),
) -> ReducedRun:
    return ReducedRun(
        project_id=NotBlankStr("proj-1"),
        task_id=NotBlankStr("task-1"),
        execution_id=NotBlankStr("exec-1"),
        brief_title=NotBlankStr("Ship checkout"),
        final_status=TaskStatus.COMPLETED,
        metrics=(RunMetric(name="Turns", value="12"),),
        decisions=decisions,
        contributions=contributions,
        outcomes=("Final status: completed",),
        open_items=open_items,
        sources=sources,
    )


def _prose() -> NarrativeProse:
    return NarrativeProse(
        summary="The team shipped checkout.",
        decisions="One decision shaped the run.",
        contributions="Two agents collaborated.",
        outcomes="The brief completed.",
    )


class TestAssembleBlocks:
    def test_opens_with_summary_then_metrics(self) -> None:
        blocks = assemble_blocks(_run(), _prose())
        assert isinstance(blocks[0], HeadingBlock)
        assert blocks[0].text == SECTION_SUMMARY
        assert isinstance(blocks[1], ProseBlock)
        assert blocks[1].text == "The team shipped checkout."
        assert any(isinstance(b, MetricBlock) for b in blocks)

    def test_decision_block_and_detail_rendered(self) -> None:
        blocks = assemble_blocks(_run(decisions=(_decision(),)), _prose())
        decision_blocks = [b for b in blocks if isinstance(b, DecisionBlock)]
        assert len(decision_blocks) == 1
        assert decision_blocks[0].decision == "Event-sourced ledger"
        assert decision_blocks[0].rationale == "Auditability wins."
        bullet_text = [
            item for b in blocks if isinstance(b, BulletListBlock) for item in b.items
        ]
        assert any("Considered: CRUD ledger" in t for t in bullet_text)
        assert any("Source: Brain entry e7" in t for t in bullet_text)

    def test_no_decisions_placeholder(self) -> None:
        blocks = assemble_blocks(_run(), _prose())
        prose_texts = [b.text for b in blocks if isinstance(b, ProseBlock)]
        assert any("No decisions were recorded" in t for t in prose_texts)

    def test_contributions_rendered(self) -> None:
        blocks = assemble_blocks(
            _run(
                contributions=(
                    AgentContribution(
                        agent_id=NotBlankStr("agent-a"),
                        turn_count=10,
                        cost=1.5,
                        tools=("read", "write"),
                    ),
                )
            ),
            _prose(),
        )
        bullet_text = [
            item for b in blocks if isinstance(b, BulletListBlock) for item in b.items
        ]
        assert any(
            "agent-a: 10 turn(s)" in t and "read, write" in t for t in bullet_text
        )

    def test_open_items_section_only_when_present(self) -> None:
        without = assemble_blocks(_run(), _prose())
        assert all(
            not (isinstance(b, HeadingBlock) and b.text == SECTION_OPEN_ITEMS)
            for b in without
        )
        with_items = assemble_blocks(
            _run(
                open_items=(
                    OpenItem(
                        kind=NotBlankStr("risk"),
                        title="Latency",
                        status=NotBlankStr("active"),
                    ),
                )
            ),
            _prose(),
        )
        assert any(
            isinstance(b, HeadingBlock) and b.text == SECTION_OPEN_ITEMS
            for b in with_items
        )

    def test_sources_section_emits_links(self) -> None:
        blocks = assemble_blocks(
            _run(
                sources=(
                    SourceRef(
                        label="Task t1", url="#task-t1", kind=NotBlankStr("task")
                    ),
                )
            ),
            _prose(),
        )
        assert any(
            isinstance(b, HeadingBlock) and b.text == SECTION_SOURCES for b in blocks
        )
        links = [b for b in blocks if isinstance(b, LinkBlock)]
        assert links[0].url == "#task-t1"

    def test_prose_sections_woven(self) -> None:
        blocks = assemble_blocks(_run(decisions=(_decision(),)), _prose())
        prose_texts = [b.text for b in blocks if isinstance(b, ProseBlock)]
        assert "One decision shaped the run." in prose_texts
        assert "Two agents collaborated." in prose_texts


class TestSafeUrl:
    def test_relative_anchor_preserved(self) -> None:
        assert _safe_url("#task-t1") == "#task-t1"

    def test_https_preserved(self) -> None:
        assert _safe_url("https://example.com/x") == "https://example.com/x"

    def test_disallowed_scheme_coerced(self) -> None:
        assert _safe_url("javascript:alert(1)") == "#external-javascript"
