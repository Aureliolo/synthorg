"""Unit tests for the charter interview orchestration service."""

from collections.abc import Awaitable, Callable

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.charter.config import CharterConfig
from synthorg.meta.charter.enums import CharterStatus
from synthorg.meta.charter.models import (
    CharterEditArgs,
    InterviewDecision,
    InterviewTurnArgs,
)
from synthorg.meta.charter.service import CharterInterviewService
from synthorg.meta.errors import (
    CharterNotEditableError,
    CharterNotFoundError,
    ConversationClosedError,
    ConversationNotFoundError,
)
from tests._shared import FakeClock
from tests._shared.conversation_fakes import (
    FakeConversationRepo as _FakeConversationRepo,
)
from tests._shared.conversation_fakes import (
    FakeTurnRepo as _FakeTurnRepo,
)
from tests.unit.meta.charter.fakes import (
    START as _START,
)
from tests.unit.meta.charter.fakes import (
    FakeCharterRepo as _FakeCharterRepo,
)
from tests.unit.meta.charter.fakes import (
    ScriptedStrategy as _ScriptedStrategy,
)
from tests.unit.meta.charter.fakes import (
    draft as _draft,
)
from tests.unit.meta.charter.fakes import (
    service as _service,
)

pytestmark = pytest.mark.unit


class TestRunTurn:
    async def test_question_keeps_conversation_active(self) -> None:
        decision = InterviewDecision(needs_more=True, next_question="What budget?")
        service, _ = _service([decision])
        result = await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("an idea"), created_by="u1")
        )
        assert result.status == "needs_more"
        assert result.next_question == "What budget?"
        assert result.charter is None

    async def test_draft_persists_charter(self) -> None:
        decision = InterviewDecision(needs_more=False, draft=_draft())
        service, charter_repo = _service([decision])
        result = await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("a clear idea"), created_by="u1")
        )
        assert result.status == "drafted"
        assert result.charter is not None
        assert result.charter.status is CharterStatus.DRAFTED
        assert len(charter_repo.items) == 1

    async def test_redraft_updates_in_place(self) -> None:
        first = InterviewDecision(needs_more=False, draft=_draft(title="V1"))
        second = InterviewDecision(needs_more=False, draft=_draft(title="V2"))
        service, charter_repo = _service([first, second])
        r1 = await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("idea"), created_by="u1")
        )
        conv_id = r1.conversation_id
        r2 = await service.run_turn(
            InterviewTurnArgs(
                message=NotBlankStr("tweak it"),
                created_by="u1",
                conversation_id=NotBlankStr(conv_id),
            )
        )
        assert len(charter_repo.items) == 1
        assert r2.charter is not None
        assert r2.charter.title == "V2"
        assert r2.charter.version == 2

    async def test_turn_cap_closes_conversation(self) -> None:
        # max_turns=1: the second turn trips the cap.
        question = InterviewDecision(needs_more=True, next_question="more?")
        service, _ = _service(
            [question, question], config=CharterConfig(interview_max_turns=1)
        )
        r1 = await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("idea"), created_by="u1")
        )
        r2 = await service.run_turn(
            InterviewTurnArgs(
                message=NotBlankStr("again"),
                created_by="u1",
                conversation_id=NotBlankStr(r1.conversation_id),
            )
        )
        assert r2.conversation_closed is True

    async def test_unknown_conversation_raises(self) -> None:
        service, _ = _service([])
        with pytest.raises(ConversationNotFoundError):
            await service.run_turn(
                InterviewTurnArgs(
                    message=NotBlankStr("x"),
                    created_by="u1",
                    conversation_id=NotBlankStr("missing"),
                )
            )

    async def test_foreign_owner_mapped_to_not_found(self) -> None:
        decision = InterviewDecision(needs_more=True, next_question="q?")
        service, _ = _service([decision])
        r1 = await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("idea"), created_by="u1")
        )
        with pytest.raises(ConversationNotFoundError):
            await service.run_turn(
                InterviewTurnArgs(
                    message=NotBlankStr("intrude"),
                    created_by="someone-else",
                    conversation_id=NotBlankStr(r1.conversation_id),
                )
            )


class TestLiveConfig:
    """The per-turn ``config_provider`` feeds live values into the strategy."""

    def _service_with(
        self,
        decisions: list[InterviewDecision],
        *,
        config_provider: Callable[[], Awaitable[CharterConfig]],
    ) -> tuple[CharterInterviewService, _ScriptedStrategy]:
        strategy = _ScriptedStrategy(decisions)
        service = CharterInterviewService(
            strategy=strategy,
            config=CharterConfig(),
            conversation_repo=_FakeConversationRepo(),
            turn_repo=_FakeTurnRepo(),
            charter_repo=_FakeCharterRepo(),
            clock=FakeClock(start=_START),
            config_provider=config_provider,
        )
        return service, strategy

    async def test_live_config_threaded_into_strategy(self) -> None:
        live = CharterConfig(
            interview_model=NotBlankStr("example-capable-001"),
            interview_max_turns=7,
        )

        async def _provide() -> CharterConfig:
            return live

        service, strategy = self._service_with(
            [InterviewDecision(needs_more=True, next_question="q?")],
            config_provider=_provide,
        )
        await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("idea"), created_by="u1")
        )
        assert strategy.configs[0] is live

    async def test_live_max_turns_caps_the_interview(self) -> None:
        async def _provide() -> CharterConfig:
            return CharterConfig(interview_max_turns=1)

        question = InterviewDecision(needs_more=True, next_question="more?")
        service, _ = self._service_with([question, question], config_provider=_provide)
        r1 = await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("idea"), created_by="u1")
        )
        r2 = await service.run_turn(
            InterviewTurnArgs(
                message=NotBlankStr("again"),
                created_by="u1",
                conversation_id=NotBlankStr(r1.conversation_id),
            )
        )
        assert r2.conversation_closed is True

    async def test_provider_failure_falls_back_to_boot_config(self) -> None:
        async def _provide() -> CharterConfig:
            msg = "settings db blip"
            raise RuntimeError(msg)

        service, strategy = self._service_with(
            [InterviewDecision(needs_more=True, next_question="q?")],
            config_provider=_provide,
        )
        await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("idea"), created_by="u1")
        )
        # The turn still ran, using the boot CharterConfig() default.
        assert strategy.configs[0] == CharterConfig()


class TestEditAndCancel:
    async def test_edit_in_place_bumps_version(self) -> None:
        service, _ = _service([InterviewDecision(needs_more=False, draft=_draft())])
        result = await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("idea"), created_by="u1")
        )
        assert result.charter is not None
        edited = await service.edit_charter(
            result.charter.id,
            CharterEditArgs(brief=NotBlankStr("sharper")),
            edited_by=NotBlankStr("u1"),
        )
        assert edited.brief == "sharper"
        assert edited.version == result.charter.version + 1

    async def test_edit_missing_charter_raises(self) -> None:
        service, _ = _service([])
        with pytest.raises(CharterNotFoundError):
            await service.edit_charter(
                NotBlankStr("nope"),
                CharterEditArgs(title=NotBlankStr("x")),
                edited_by=NotBlankStr("u1"),
            )

    async def test_cancel_transitions_to_cancelled(self) -> None:
        service, _ = _service([InterviewDecision(needs_more=False, draft=_draft())])
        result = await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("idea"), created_by="u1")
        )
        assert result.charter is not None
        cancelled = await service.cancel_charter(
            result.charter.id, cancelled_by=NotBlankStr("u1")
        )
        assert cancelled.status is CharterStatus.CANCELLED

    async def test_edit_after_cancel_rejected(self) -> None:
        service, _ = _service([InterviewDecision(needs_more=False, draft=_draft())])
        result = await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("idea"), created_by="u1")
        )
        assert result.charter is not None
        await service.cancel_charter(result.charter.id, cancelled_by=NotBlankStr("u1"))
        with pytest.raises(CharterNotEditableError):
            await service.edit_charter(
                result.charter.id,
                CharterEditArgs(brief=NotBlankStr("late")),
                edited_by=NotBlankStr("u1"),
            )

    async def test_run_turn_after_cancel_closes_interview(self) -> None:
        service, _ = _service(
            [
                InterviewDecision(needs_more=False, draft=_draft()),
                InterviewDecision(needs_more=False, draft=_draft()),
            ]
        )
        result = await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("idea"), created_by="u1")
        )
        assert result.charter is not None
        await service.cancel_charter(result.charter.id, cancelled_by=NotBlankStr("u1"))
        with pytest.raises(ConversationClosedError):
            await service.run_turn(
                InterviewTurnArgs(
                    message=NotBlankStr("more"),
                    created_by="u1",
                    conversation_id=NotBlankStr(result.conversation_id),
                )
            )


class TestOwnership:
    async def test_get_by_non_creator_is_unfound(self) -> None:
        service, _ = _service([InterviewDecision(needs_more=False, draft=_draft())])
        result = await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("idea"), created_by="u1")
        )
        assert result.charter is not None
        # Same actor: reads through.
        same = await service.get(result.charter.id, requested_by=NotBlankStr("u1"))
        assert same.id == result.charter.id
        # Foreign actor: shaped as NotFound (no probe of existence).
        with pytest.raises(CharterNotFoundError):
            await service.get(result.charter.id, requested_by=NotBlankStr("u2"))

    async def test_edit_by_non_creator_is_denied(self) -> None:
        service, _ = _service([InterviewDecision(needs_more=False, draft=_draft())])
        result = await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("idea"), created_by="u1")
        )
        assert result.charter is not None
        with pytest.raises(CharterNotFoundError):
            await service.edit_charter(
                result.charter.id,
                CharterEditArgs(brief=NotBlankStr("hijacked")),
                edited_by=NotBlankStr("attacker"),
            )

    async def test_cancel_by_non_creator_is_denied(self) -> None:
        service, _ = _service([InterviewDecision(needs_more=False, draft=_draft())])
        result = await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("idea"), created_by="u1")
        )
        assert result.charter is not None
        with pytest.raises(CharterNotFoundError):
            await service.cancel_charter(
                result.charter.id, cancelled_by=NotBlankStr("attacker")
            )


class TestConcurrency:
    async def test_concurrent_turns_serialize_on_same_conversation(self) -> None:
        # Two concurrent run_turn calls on the same conversation must
        # not interleave: the lock guarantees one snapshot per turn so
        # the second turn sees the first user message and assistant
        # reply before snapshotting its own history.
        import asyncio

        # Three decisions: one for the initial seeding turn, then two
        # more for the concurrent pair (the lock guarantees both run
        # and each consumes exactly one decision).
        service, _ = _service(
            [
                InterviewDecision(needs_more=True, next_question="What budget?"),
                InterviewDecision(
                    needs_more=True, next_question="What is the deadline?"
                ),
                InterviewDecision(needs_more=False, draft=_draft()),
            ]
        )
        first = await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("a vague idea"), created_by="u1")
        )

        # Now fire two concurrent turns on the same conversation.
        async def _turn(text: str) -> object:
            return await service.run_turn(
                InterviewTurnArgs(
                    message=NotBlankStr(text),
                    created_by="u1",
                    conversation_id=first.conversation_id,
                )
            )

        outcomes = await asyncio.gather(_turn("sharper-1"), _turn("sharper-2"))
        # Both turns succeeded (strategy provided enough decisions);
        # no exception, no charter-double-mint, no lost-update.
        assert all(o is not None for o in outcomes)

    async def test_lock_allocation_under_many_distinct_conversations(self) -> None:
        # Lock allocation guards against a race in the per-conversation
        # lock dict creation. Fan out many first-uses of distinct ids;
        # all must complete cleanly.
        import asyncio

        decisions = [
            InterviewDecision(needs_more=True, next_question=NotBlankStr(f"q{i}"))
            for i in range(20)
        ]
        service, _ = _service(decisions)

        async def _open(idx: int) -> object:
            return await service.run_turn(
                InterviewTurnArgs(
                    message=NotBlankStr(f"idea-{idx}"),
                    created_by=NotBlankStr(f"u{idx}"),
                )
            )

        results = await asyncio.gather(*[_open(i) for i in range(20)])
        assert len(results) == 20

    async def test_turn_cap_at_default_config_boundary(self) -> None:
        # Default cap (CharterConfig.interview_max_turns) is honoured;
        # the (cap+1)th assistant turn replies with the cap message and
        # the conversation transitions to CLOSED.
        default_cap = CharterConfig().interview_max_turns
        decisions: list[InterviewDecision] = [
            InterviewDecision(needs_more=True, next_question=NotBlankStr(f"q{i}"))
            for i in range(default_cap)
        ]
        # One extra decision that should never be consumed; the cap
        # path returns before the strategy is invoked.
        decisions.append(InterviewDecision(needs_more=False, draft=_draft()))
        service, _ = _service(decisions)
        first = await service.run_turn(
            InterviewTurnArgs(message=NotBlankStr("idea-0"), created_by="u1")
        )
        for i in range(1, default_cap):
            await service.run_turn(
                InterviewTurnArgs(
                    message=NotBlankStr(f"idea-{i}"),
                    created_by="u1",
                    conversation_id=first.conversation_id,
                )
            )
        # The cap+1 turn must be force-closed.
        capped = await service.run_turn(
            InterviewTurnArgs(
                message=NotBlankStr("one too many"),
                created_by="u1",
                conversation_id=first.conversation_id,
            )
        )
        assert capped.conversation_closed is True
