"""Unit tests for the run-narrative assembler."""

import pytest

from synthorg.budget.currency import format_cost
from synthorg.core.task_enums import TaskStatus
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
from synthorg.project_brain.models import BrainEntryKind, BrainEntryStatus

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

    def test_no_contributions_placeholder(self) -> None:
        blocks = assemble_blocks(_run(), _prose())
        prose_texts = [b.text for b in blocks if isinstance(b, ProseBlock)]
        assert any("No agent activity was recorded" in t for t in prose_texts)

    def test_contribution_cost_carries_currency(self) -> None:
        run = _run(
            contributions=(
                AgentContribution(
                    agent_id=NotBlankStr("agent-a"), turn_count=3, cost=1.5
                ),
            )
        )
        blocks = assemble_blocks(run, _prose())
        bullet_text = [
            item for b in blocks if isinstance(b, BulletListBlock) for item in b.items
        ]
        # The cost renders via the canonical ``format_cost`` helper (symbol +
        # amount) in the run's effective currency, never as a bare number and
        # never privileging a specific currency symbol.
        assert any(f"cost {format_cost(1.5, run.currency)}" in t for t in bullet_text)

    def test_malicious_source_url_coerced_end_to_end(self) -> None:
        # A javascript: or protocol-relative citation that flows through
        # the assembler must reach the LinkBlock as a non-navigable anchor.
        blocks = assemble_blocks(
            _run(
                sources=(
                    SourceRef(
                        label="Evil", url="javascript:alert(1)", kind=NotBlankStr("x")
                    ),
                    SourceRef(
                        label="Redirect",
                        url="//evil.example.com",
                        kind=NotBlankStr("x"),
                    ),
                )
            ),
            _prose(),
        )
        urls = {b.url for b in blocks if isinstance(b, LinkBlock)}
        assert urls == {"#external-javascript", "#external-protocol-relative"}

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
                        kind=BrainEntryKind.RISK,
                        title="Latency",
                        status=BrainEntryStatus.ACTIVE,
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

    def test_protocol_relative_coerced(self) -> None:
        # `//host` has no scheme but the browser resolves it against the
        # page protocol: an open-redirect vector that must be coerced.
        assert _safe_url("//evil.example.com/x") == "#external-protocol-relative"

    def test_relative_path_preserved(self) -> None:
        assert _safe_url("../sibling/doc") == "../sibling/doc"

    def test_leading_whitespace_protocol_relative_coerced(self) -> None:
        # Browsers trim leading whitespace from href, so " //evil" would
        # bypass a naive startswith check and resolve as an open-redirect.
        assert _safe_url(" //evil.example.com/x") == "#external-protocol-relative"

    def test_leading_whitespace_disallowed_scheme_coerced(self) -> None:
        assert _safe_url("  javascript:alert(1)") == "#external-javascript"

    def test_leading_whitespace_https_preserved(self) -> None:
        # A permitted scheme behind whitespace stays navigable.
        assert _safe_url(" https://example.com/x") == " https://example.com/x"

    @pytest.mark.parametrize(
        "raw",
        [r"\\evil.example.com", r"/\evil.example.com", r"\/evil.example.com"],
    )
    def test_backslash_authority_coerced(self, raw: str) -> None:
        # Browsers normalise backslashes to forward slashes, so a
        # backslash-authority form is an open-redirect vector just like
        # ``//host`` and must be coerced.
        assert _safe_url(raw) == "#external-protocol-relative"

    def test_leading_whitespace_backslash_authority_coerced(self) -> None:
        assert _safe_url("  \\\\evil.example.com") == "#external-protocol-relative"
