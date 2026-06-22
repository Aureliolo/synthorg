"""Tests for epistemic marker preservation in _build_summary."""

from datetime import date

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig, PersonalityConfig
from synthorg.core.task_enums import Complexity
from synthorg.engine.compaction.models import CompactionConfig
from synthorg.engine.compaction.summarizer import _build_summary, force_compaction
from synthorg.engine.context import AgentContext
from synthorg.engine.token_estimation import DefaultTokenEstimator
from synthorg.hr.seniority import SeniorityLevel
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage


def _msg(role: MessageRole, content: str) -> ChatMessage:
    """Create a chat message."""
    return ChatMessage(role=role, content=content)


@pytest.mark.unit
class TestBuildSummaryMarkers:
    """Tests for _build_summary with epistemic marker preservation."""

    def test_no_markers_standard_format(self) -> None:
        """Messages without markers produce standard format."""
        messages = (
            _msg(MessageRole.ASSISTANT, "The answer is 42."),
            _msg(MessageRole.USER, "Thanks"),
            _msg(
                MessageRole.ASSISTANT,
                "This is a straightforward solution with no reasoning.",
            ),
        )
        summary = _build_summary(
            messages,
            preserve_markers=True,
            task_complexity=Complexity.COMPLEX,
        )

        assert "[Archived 3 messages. Summary of prior work:" in summary

    def test_with_markers_complex_preserved(self) -> None:
        """Message with marker + COMPLEX complexity -> preserved."""
        messages = (_msg(MessageRole.ASSISTANT, "Wait, I need to reconsider this."),)
        summary = _build_summary(
            messages,
            preserve_markers=True,
            task_complexity=Complexity.COMPLEX,
        )

        # Should have epistemic markers preserved format
        assert "Epistemic markers preserved" in summary
        assert "Wait" in summary or "reconsider" in summary

    def test_with_markers_epic_preserved(self) -> None:
        """Message with marker + EPIC complexity -> preserved."""
        messages = (_msg(MessageRole.ASSISTANT, "Hmm, let me verify this."),)
        summary = _build_summary(
            messages,
            preserve_markers=True,
            task_complexity=Complexity.EPIC,
        )

        assert "Epistemic markers preserved" in summary

    def test_with_one_marker_simple_not_preserved(self) -> None:
        """Message with 1 marker + SIMPLE complexity -> NOT preserved."""
        messages = (_msg(MessageRole.ASSISTANT, "Wait, I see the issue."),)
        summary = _build_summary(
            messages,
            preserve_markers=True,
            task_complexity=Complexity.SIMPLE,
        )

        # Should use standard format (threshold is 3 for SIMPLE)
        assert "Summary of prior work:" in summary
        assert "Epistemic markers preserved" not in summary

    def test_with_three_markers_simple_preserved(self) -> None:
        """Message with 3 markers + SIMPLE complexity -> preserved."""
        messages = (
            _msg(
                MessageRole.ASSISTANT,
                "Wait, actually I was wrong. Let me verify this.",
            ),
        )
        summary = _build_summary(
            messages,
            preserve_markers=True,
            task_complexity=Complexity.SIMPLE,
        )

        assert "Epistemic markers preserved" in summary

    def test_with_markers_medium_below_threshold(self) -> None:
        """Message with 2 markers + MEDIUM complexity -> NOT preserved."""
        # "hmm" (hedging) + "perhaps" (uncertainty) = 2 groups < 3 threshold
        messages = (_msg(MessageRole.ASSISTANT, "Hmm, perhaps we should try again."),)
        summary = _build_summary(
            messages,
            preserve_markers=True,
            task_complexity=Complexity.MEDIUM,
        )

        assert "Summary of prior work:" in summary
        assert "Epistemic markers preserved" not in summary

    def test_markers_disabled_standard_format(self) -> None:
        """preserve_markers=False -> standard format even with markers."""
        messages = (
            _msg(
                MessageRole.ASSISTANT,
                "Wait, actually let me verify. This is important.",
            ),
        )
        summary = _build_summary(
            messages,
            preserve_markers=False,
            task_complexity=Complexity.COMPLEX,
        )

        # Even with COMPLEX, if preserve_markers is False, use standard format
        assert "Summary of prior work:" in summary
        assert "Epistemic markers preserved" not in summary

    def test_empty_messages_fallback(self) -> None:
        """No assistant messages -> fallback format."""
        messages = (
            _msg(MessageRole.USER, "What's the answer?"),
            _msg(MessageRole.SYSTEM, "You are helpful."),
        )
        summary = _build_summary(
            messages,
            preserve_markers=True,
            task_complexity=Complexity.COMPLEX,
        )

        assert "[Archived 2 messages from earlier" in summary

    def test_preserved_count_in_summary(self) -> None:
        """Summary mentions count of preserved messages."""
        messages = (
            _msg(
                MessageRole.ASSISTANT,
                "Wait, I need to reconsider this approach.",
            ),
            _msg(MessageRole.USER, "Ok"),
            _msg(
                MessageRole.ASSISTANT,
                "Actually, let me verify the calculations.",
            ),
        )
        summary = _build_summary(
            messages,
            preserve_markers=True,
            task_complexity=Complexity.COMPLEX,
        )

        # Should mention 2 preserved messages
        assert "Epistemic markers preserved from 2 messages" in summary

    def test_mixed_preserved_and_standard(self) -> None:
        """Mix of preserved markers and standard snippets."""
        messages = (
            _msg(MessageRole.ASSISTANT, "Wait, I need to think about this."),
            _msg(
                MessageRole.ASSISTANT,
                "This is just a straightforward statement.",
            ),
        )
        summary = _build_summary(
            messages,
            preserve_markers=True,
            task_complexity=Complexity.COMPLEX,
        )

        # Should have preserved markers format
        assert "Epistemic markers preserved" in summary
        # Should have summary content
        assert len(summary) > 50

    def test_marker_sentences_joined_correctly(self) -> None:
        """Extracted marker sentences are joined in summary."""
        messages = (
            _msg(
                MessageRole.ASSISTANT,
                "This is normal. Wait, let me reconsider. More normal text.",
            ),
        )
        summary = _build_summary(
            messages,
            preserve_markers=True,
            task_complexity=Complexity.COMPLEX,
        )

        # Should contain the extracted marker sentence
        assert "Wait" in summary or "reconsider" in summary

    def test_system_messages_ignored(self) -> None:
        """SYSTEM messages don't contribute to summary."""
        messages = (
            _msg(MessageRole.SYSTEM, "System instruction here"),
            _msg(MessageRole.ASSISTANT, "Response content"),
        )
        summary = _build_summary(
            messages,
            preserve_markers=True,
            task_complexity=Complexity.COMPLEX,
        )

        # Should archive both but only extract from ASSISTANT
        assert "[Archived 2 messages" in summary

    def test_user_messages_ignored(self) -> None:
        """USER messages don't contribute to summary."""
        messages = (
            _msg(MessageRole.USER, "User query here"),
            _msg(MessageRole.ASSISTANT, "Wait, let me think about this."),
        )
        summary = _build_summary(
            messages,
            preserve_markers=True,
            task_complexity=Complexity.COMPLEX,
        )

        # USER message is archived but not summarized
        assert "Epistemic markers preserved" in summary

    def test_empty_assistant_content_ignored(self) -> None:
        """Empty assistant messages are skipped."""
        messages = (
            _msg(MessageRole.ASSISTANT, ""),
            _msg(MessageRole.ASSISTANT, "Wait, something important."),
        )
        summary = _build_summary(
            messages,
            preserve_markers=True,
            task_complexity=Complexity.COMPLEX,
        )

        # First message is empty and skipped, second has marker
        assert "Epistemic markers preserved from 1 message." in summary

    def test_summary_respects_max_length(self) -> None:
        """Summary is truncated at MAX_SUMMARY_CHARS."""
        # Create very long content
        long_content = "Wait, " + "x" * 600
        messages = (_msg(MessageRole.ASSISTANT, long_content),)
        summary = _build_summary(
            messages,
            preserve_markers=True,
            task_complexity=Complexity.COMPLEX,
        )

        # Summary should be reasonable length (max 500 chars for content)
        assert len(summary) < 1000


def _make_identity(name: str = "test-agent") -> AgentIdentity:
    """Create a test agent identity."""
    return AgentIdentity(
        name=name,
        role="engineer",
        department="engineering",
        level=SeniorityLevel.MID,
        hiring_date=date(2026, 1, 15),
        personality=PersonalityConfig(traits=("analytical",)),
        model=ModelConfig(
            provider="test-provider",
            model_id="test-small-001",
        ),
    )


@pytest.mark.unit
class TestForceCompaction:
    """Tests for force_compaction function."""

    async def test_force_compaction_too_few_messages(self) -> None:
        """force_compaction returns None with too few messages."""
        identity = _make_identity()
        ctx = AgentContext.from_identity(identity)
        # Add only 2 messages (below default min_messages_to_compact=4)
        ctx = ctx.with_message(
            ChatMessage(role=MessageRole.USER, content="Hello"),
        )
        ctx = ctx.with_message(
            ChatMessage(role=MessageRole.ASSISTANT, content="Hi"),
        )

        config = CompactionConfig(min_messages_to_compact=4)
        estimator = DefaultTokenEstimator()

        result = await force_compaction(ctx, config, estimator)

        assert result is None

    async def test_force_compaction_bypasses_threshold(self) -> None:
        """force_compaction runs even when fill is below threshold."""
        identity = _make_identity()
        ctx = AgentContext.from_identity(identity)
        # Add 8 messages to ensure we have enough for compaction
        for i in range(8):
            if i % 2 == 0:
                msg = ChatMessage(
                    role=MessageRole.USER,
                    content=f"User message {i}",
                )
            else:
                msg = ChatMessage(
                    role=MessageRole.ASSISTANT,
                    content=f"Response {i}",
                )
            ctx = ctx.with_message(msg)

        config = CompactionConfig(
            min_messages_to_compact=4,
            fill_threshold_percent=95.0,  # Very high threshold
        )
        estimator = DefaultTokenEstimator()

        # Should succeed despite low fill percentage
        result = await force_compaction(ctx, config, estimator)

        # Forced compaction should produce a compacted context
        assert result is not None
        assert isinstance(result, AgentContext)
        assert len(result.conversation) < len(ctx.conversation)
