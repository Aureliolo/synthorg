"""Tests for context budget indicators and fill estimation."""

import pytest
from pydantic import ValidationError

from synthorg.core.agent import AgentIdentity
from synthorg.engine.compaction.models import CompressionMetadata
from synthorg.engine.context import AgentContext
from synthorg.engine.context_budget import (
    _TOOL_DEFINITION_TOKEN_OVERHEAD,
    ContextBudgetIndicator,
    estimate_context_fill,
    make_context_indicator,
    update_context_fill,
)
from synthorg.engine.token_estimation import DefaultTokenEstimator
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

# ── ContextBudgetIndicator ────────────────────────────────────────


@pytest.mark.unit
class TestContextBudgetIndicator:
    """ContextBudgetIndicator model and formatting."""

    def test_format_known_capacity(self) -> None:
        ind = ContextBudgetIndicator(
            fill_tokens=12_450,
            capacity_tokens=16_000,
            archived_blocks=0,
        )
        result = ind.format()
        assert "12,450" in result
        assert "16,000" in result
        assert "78%" in result
        assert "0 archived blocks" in result

    def test_format_unknown_capacity(self) -> None:
        ind = ContextBudgetIndicator(
            fill_tokens=5_000,
            archived_blocks=2,
        )
        result = ind.format()
        assert "5,000" in result
        assert "capacity unknown" in result
        assert "2 archived blocks" in result

    def test_fill_percent_known(self) -> None:
        ind = ContextBudgetIndicator(
            fill_tokens=8_000,
            capacity_tokens=10_000,
        )
        assert ind.fill_percent == pytest.approx(80.0)

    def test_fill_percent_unknown(self) -> None:
        ind = ContextBudgetIndicator(fill_tokens=8_000)
        assert ind.fill_percent is None

    def test_fill_percent_zero_fill(self) -> None:
        ind = ContextBudgetIndicator(
            fill_tokens=0,
            capacity_tokens=10_000,
        )
        assert ind.fill_percent == pytest.approx(0.0)

    def test_frozen(self) -> None:
        ind = ContextBudgetIndicator(fill_tokens=100)
        with pytest.raises(ValidationError):
            ind.fill_tokens = 200  # type: ignore[misc]

    def test_capacity_tokens_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ContextBudgetIndicator(fill_tokens=100, capacity_tokens=0)

    def test_no_ceiling_carries_no_budget_segment(self) -> None:
        # The unbounded case: identical to the pre-A2 shape, so a session
        # with no token ceiling reads exactly as it always has.
        ind = ContextBudgetIndicator(fill_tokens=12_450, capacity_tokens=16_000)
        assert "Budget" not in ind.format()
        assert ind.spend_percent is None

    def test_format_includes_token_spend_against_ceiling(self) -> None:
        ind = ContextBudgetIndicator(
            fill_tokens=1_000,
            spend_tokens=340_000,
            token_ceiling=1_500_000,
        )
        result = ind.format()
        assert "340,000" in result
        assert "1,500,000" in result
        assert "23%" in result

    def test_spend_percent_computed(self) -> None:
        ind = ContextBudgetIndicator(
            fill_tokens=0,
            spend_tokens=750_000,
            token_ceiling=1_500_000,
        )
        assert ind.spend_percent == pytest.approx(50.0)

    def test_spend_percent_none_without_ceiling(self) -> None:
        ind = ContextBudgetIndicator(fill_tokens=0, spend_tokens=1_000)
        assert ind.spend_percent is None

    def test_token_ceiling_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ContextBudgetIndicator(fill_tokens=100, token_ceiling=0)


# ── estimate_context_fill ─────────────────────────────────────────


@pytest.mark.unit
class TestEstimateContextFill:
    """Context fill estimation."""

    def test_empty_conversation(self) -> None:
        result = estimate_context_fill(
            system_prompt_tokens=100,
            conversation=(),
            tool_definitions_count=0,
        )
        assert result == 100

    def test_with_messages(self) -> None:
        msgs = (
            ChatMessage(
                role=MessageRole.USER,
                content="a" * 40,
            ),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="b" * 80,
            ),
        )
        est = DefaultTokenEstimator()
        conv_tokens = est.estimate_conversation_tokens(msgs)
        result = estimate_context_fill(
            system_prompt_tokens=50,
            conversation=msgs,
            tool_definitions_count=0,
        )
        assert result == 50 + conv_tokens

    def test_with_tools(self) -> None:
        result = estimate_context_fill(
            system_prompt_tokens=100,
            conversation=(),
            tool_definitions_count=3,
        )
        assert result == 100 + 3 * _TOOL_DEFINITION_TOKEN_OVERHEAD


# ── make_context_indicator ────────────────────────────────────────


@pytest.mark.unit
class TestMakeContextIndicator:
    """make_context_indicator factory."""

    def test_from_context_with_capacity(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = sample_agent_context.model_copy(
            update={
                "context_fill_tokens": 5_000,
                "context_capacity_tokens": 10_000,
            },
        )
        ind = make_context_indicator(ctx)
        assert ind.fill_tokens == 5_000
        assert ind.capacity_tokens == 10_000
        assert ind.archived_blocks == 0

    def test_from_context_without_capacity(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ind = make_context_indicator(sample_agent_context)
        assert ind.fill_tokens == 0
        assert ind.capacity_tokens is None
        assert ind.archived_blocks == 0

    def test_from_context_with_token_ceiling(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = sample_agent_context.model_copy(update={"token_ceiling": 1_500_000})
        ctx = ctx.model_copy(
            update={
                "accumulated_cost": ctx.accumulated_cost.model_copy(
                    update={"input_tokens": 340_000}
                ),
            }
        )
        ind = make_context_indicator(ctx)
        assert ind.token_ceiling == 1_500_000
        assert ind.spend_tokens == 340_000

    def test_from_context_without_token_ceiling_has_no_spend(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        # A run with no token ceiling has nothing to report a share of; the
        # spend axis stays absent rather than reporting against nothing.
        ind = make_context_indicator(sample_agent_context)
        assert ind.token_ceiling is None
        assert ind.spend_tokens is None


# ── update_context_fill ───────────────────────────────────────────


@pytest.mark.unit
class TestUpdateContextFill:
    """update_context_fill helper."""

    def test_updates_fill_tokens(
        self,
        sample_agent_context: AgentContext,
    ) -> None:
        ctx = sample_agent_context.model_copy(
            update={"context_capacity_tokens": 10_000},
        )
        msgs = (ChatMessage(role=MessageRole.SYSTEM, content="x" * 400),)
        ctx = ctx.model_copy(update={"conversation": msgs})
        updated = update_context_fill(
            ctx,
            system_prompt_tokens=200,
            tool_defs_count=2,
        )
        assert updated.context_fill_tokens > 0
        assert updated.context_fill_tokens != ctx.context_fill_tokens


# ── DefaultTokenEstimator.estimate_conversation_tokens ────────────


@pytest.mark.unit
class TestEstimateConversationTokens:
    """DefaultTokenEstimator.estimate_conversation_tokens."""

    def test_empty(self) -> None:
        est = DefaultTokenEstimator()
        assert est.estimate_conversation_tokens(()) == 0

    def test_single_message(self) -> None:
        est = DefaultTokenEstimator()
        msgs = (ChatMessage(role=MessageRole.USER, content="a" * 100),)
        result = est.estimate_conversation_tokens(msgs)
        # 100 chars / 4 = 25, + 4 overhead = 29
        assert result == 29

    def test_none_content(self) -> None:
        est = DefaultTokenEstimator()
        msgs = (
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="",
            ),
        )
        result = est.estimate_conversation_tokens(msgs)
        # empty content => 0 + 4 overhead = 4
        assert result == 4


# ── AgentContext context budget fields ────────────────────────────


@pytest.mark.unit
class TestAgentContextBudgetFields:
    """AgentContext context budget field tests."""

    def test_context_fill_percent_with_capacity(
        self,
        sample_agent: AgentIdentity,
    ) -> None:
        ctx = AgentContext.from_identity(
            sample_agent,
            context_capacity_tokens=10_000,
        ).model_copy(update={"context_fill_tokens": 5_000})
        assert ctx.context_fill_percent == pytest.approx(50.0)

    def test_context_fill_percent_without_capacity(
        self,
        sample_agent: AgentIdentity,
    ) -> None:
        ctx = AgentContext.from_identity(sample_agent)
        assert ctx.context_fill_percent is None

    def test_with_context_fill(
        self,
        sample_agent: AgentIdentity,
    ) -> None:
        ctx = AgentContext.from_identity(sample_agent)
        updated = ctx.with_context_fill(500)
        assert updated.context_fill_tokens == 500
        assert ctx.context_fill_tokens == 0  # original unchanged

    def test_with_compression(
        self,
        sample_agent: AgentIdentity,
    ) -> None:
        ctx = AgentContext.from_identity(
            sample_agent,
            context_capacity_tokens=10_000,
        )
        msg = ChatMessage(role=MessageRole.SYSTEM, content="summary")
        metadata = CompressionMetadata(
            compression_point=5,
            archived_turns=3,
            summary_tokens=10,
        )
        updated = ctx.with_compression(metadata, (msg,), 100, pinned=frozenset())
        assert updated.compression_metadata is metadata
        assert updated.conversation == (msg,)
        assert updated.context_fill_tokens == 100
        assert ctx.compression_metadata is None  # original unchanged

    def test_from_identity_with_capacity(
        self,
        sample_agent: AgentIdentity,
    ) -> None:
        ctx = AgentContext.from_identity(
            sample_agent,
            context_capacity_tokens=200_000,
        )
        assert ctx.context_capacity_tokens == 200_000

    def test_snapshot_includes_fill_fields(
        self,
        sample_agent: AgentIdentity,
    ) -> None:
        ctx = AgentContext.from_identity(
            sample_agent,
            context_capacity_tokens=10_000,
        ).model_copy(update={"context_fill_tokens": 3_000})
        snapshot = ctx.to_snapshot()
        assert snapshot.context_fill_tokens == 3_000
        assert snapshot.context_fill_percent == pytest.approx(30.0)

    def test_make_context_indicator_with_compression(
        self,
        sample_agent: AgentIdentity,
    ) -> None:
        metadata = CompressionMetadata(
            compression_point=5,
            archived_turns=3,
            summary_tokens=10,
            compactions_performed=2,
        )
        ctx = AgentContext.from_identity(
            sample_agent,
            context_capacity_tokens=10_000,
        ).model_copy(
            update={
                "context_fill_tokens": 5_000,
                "compression_metadata": metadata,
            },
        )
        ind = make_context_indicator(ctx)
        assert ind.archived_blocks == 2
