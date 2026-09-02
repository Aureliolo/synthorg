"""The task brief is pinned, and a pin survives whatever compaction does."""

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.engine.compaction._conversation import split_conversation
from synthorg.engine.compaction.models import CompactionConfig, CompressionMetadata
from synthorg.engine.compaction.summarizer import make_compaction_callback
from synthorg.engine.context import AgentContext
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

pytestmark = pytest.mark.unit

_BRIEF = "# Task\n\nTitle: build the parser\n\nParse the thing."


def _msg(role: MessageRole, content: str) -> ChatMessage:
    return ChatMessage(role=role, content=content)


def _turns(count: int, *, label: str) -> tuple[ChatMessage, ...]:
    return tuple(
        _msg(
            MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT,
            f"{label} {i} " * 20,
        )
        for i in range(count)
    )


def _seeded(identity: AgentIdentity, *, turns: int) -> AgentContext:
    """A context whose brief is pinned the way the engine seeds it."""
    ctx = AgentContext.from_identity(identity, context_capacity_tokens=1000)
    ctx = ctx.model_copy(
        update={"conversation": (_msg(MessageRole.SYSTEM, "system prompt"),)}
    )
    ctx = ctx.with_pinned_message(_msg(MessageRole.USER, _BRIEF))
    return ctx.model_copy(
        update={
            "conversation": (*ctx.conversation, *_turns(turns, label="turn")),
            "context_fill_tokens": 900,
            "turn_count": turns // 2,
        }
    )


def _callback() -> object:
    return make_compaction_callback(
        config=CompactionConfig(
            fill_threshold_percent=80.0,
            min_messages_to_compact=4,
            preserve_recent_turns=1,
        )
    )


def _pinned_contents(ctx: AgentContext) -> tuple[str | None, ...]:
    pinned = sorted(ctx.pinned_message_indices)
    return tuple(ctx.conversation[i].content for i in pinned)


class TestPinnedBriefSurvivesCompaction:
    """A pinned message is never archived, wherever it sits."""

    def test_seeding_pins_the_brief_at_its_index(
        self, sample_agent: AgentIdentity
    ) -> None:
        ctx = _seeded(sample_agent, turns=0)
        assert ctx.pinned_message_indices == frozenset({1})
        assert ctx.conversation[1].content == _BRIEF

    def test_a_span_that_is_all_pins_compacts_nothing(
        self, sample_agent: AgentIdentity
    ) -> None:
        """Archiving nothing and splicing a summary in would grow the context.

        With the recent window covering every turn, the only candidate is the
        pinned brief; a compaction here would add a summary of nothing on
        every pass and never bring the fill down.
        """
        ctx = _seeded(sample_agent, turns=2)
        config = CompactionConfig(
            fill_threshold_percent=80.0,
            min_messages_to_compact=1,
            preserve_recent_turns=1,
        )

        assert split_conversation(ctx, config) is None

    async def test_pin_survives_one_compaction(
        self, sample_agent: AgentIdentity
    ) -> None:
        ctx = _seeded(sample_agent, turns=12)
        callback = _callback()
        result = await callback(ctx)  # type: ignore[operator]
        assert result is not None
        # The brief would have been the first archivable message. It is
        # rescued and re-seated between the system head and the summary.
        assert _pinned_contents(result) == (_BRIEF,)
        assert result.conversation[0].content == "system prompt"
        assert result.conversation[1].content == _BRIEF
        assert result.conversation[2].role == MessageRole.SYSTEM
        assert "Archived" in (result.conversation[2].content or "")
        assert len(result.pinned_message_indices) == 1

    async def test_pin_indices_stay_correct_after_two_compactions(
        self, sample_agent: AgentIdentity
    ) -> None:
        ctx = _seeded(sample_agent, turns=12)
        callback = _callback()
        first = await callback(ctx)  # type: ignore[operator]
        assert first is not None
        grown = first.model_copy(
            update={
                "conversation": (*first.conversation, *_turns(8, label="later")),
                "context_fill_tokens": 900,
            }
        )
        second = await callback(grown)  # type: ignore[operator]
        assert second is not None
        assert second.compression_metadata is not None
        assert second.compression_metadata.compactions_performed == 2
        # After the second pass the brief still names exactly itself, at
        # whatever index the rebuilt list put it, and nothing else is pinned.
        assert _pinned_contents(second) == (_BRIEF,)
        archived = [m for m in second.conversation if m.content == _BRIEF]
        assert len(archived) == 1

    def test_pin_survives_a_checkpoint_round_trip(
        self, sample_agent: AgentIdentity
    ) -> None:
        ctx = _seeded(sample_agent, turns=4)
        restored = AgentContext.model_validate_json(ctx.model_dump_json())
        assert restored.pinned_message_indices == ctx.pinned_message_indices
        assert _pinned_contents(restored) == (_BRIEF,)

    def test_a_pin_outside_the_compressed_list_is_refused(
        self, sample_agent: AgentIdentity
    ) -> None:
        ctx = _seeded(sample_agent, turns=2)
        metadata = CompressionMetadata(
            compression_point=1, archived_turns=1, summary_tokens=1
        )
        with pytest.raises(ValueError, match="pinned indices outside"):
            ctx.with_compression(
                metadata,
                (_msg(MessageRole.SYSTEM, "s"),),
                10,
                pinned=frozenset({4}),
            )
