"""Unit tests for the charter interview orchestration service."""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import CharterStatus, ConversationStatus
from synthorg.core.types import NotBlankStr
from synthorg.meta.charter.config import CharterConfig
from synthorg.meta.charter.models import (
    BudgetEnvelope,
    CharterDraft,
    CharterEditArgs,
    InterviewDecision,
    InterviewTurnArgs,
    ProjectCharter,
)
from synthorg.meta.charter.service import CharterInterviewService
from synthorg.meta.chief_of_staff.models import Conversation, ConversationTurn
from synthorg.meta.errors import (
    CharterNotEditableError,
    CharterNotFoundError,
    ConversationClosedError,
    ConversationNotFoundError,
)
from synthorg.persistence.charter_protocol import CharterFilterSpec
from synthorg.persistence.conversation_protocol import ConversationTurnFilterSpec
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_START = datetime(2026, 5, 22, 9, 0, 0, tzinfo=UTC)


def _draft(**overrides: object) -> CharterDraft:
    defaults: dict[str, object] = {
        "title": "Memory layer",
        "brief": "Build a better memory layer.",
        "success_criteria": (NotBlankStr("recall +10%"),),
        "envelope": BudgetEnvelope(amount=5000.0, currency="USD"),
        "proposed_project_name": "memory-layer",
    }
    defaults.update(overrides)
    return CharterDraft(**defaults)  # type: ignore[arg-type]


class _FakeConversationRepo:
    def __init__(self) -> None:
        self.items: dict[str, Conversation] = {}

    async def save(self, entity: Conversation) -> None:
        self.items[entity.id] = entity

    async def get(self, entity_id: str) -> Conversation | None:
        return self.items.get(entity_id)

    async def delete(self, entity_id: str) -> bool:
        return self.items.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[Conversation, ...]:
        return tuple(self.items.values())[offset : offset + limit]

    async def transition_if(
        self,
        entity_id: str,
        from_state: ConversationStatus,
        to_state: ConversationStatus,
        **updates: object,
    ) -> bool:
        current = self.items.get(entity_id)
        if current is None or current.status is not from_state:
            return False
        self.items[entity_id] = current.model_copy(update={"status": to_state})
        return True


class _FakeTurnRepo:
    def __init__(self) -> None:
        self.turns: list[ConversationTurn] = []

    async def append(self, event: ConversationTurn) -> None:
        self.turns.append(event)

    async def query(
        self,
        filter_spec: ConversationTurnFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ConversationTurn, ...]:
        rows = [
            t
            for t in self.turns
            if filter_spec.conversation_id is None
            or t.conversation_id == filter_spec.conversation_id
        ]
        rows.sort(key=lambda t: t.sequence, reverse=True)
        return tuple(rows[offset : offset + limit])

    async def purge_before(self, threshold: datetime) -> int:
        before = len(self.turns)
        self.turns = [t for t in self.turns if t.created_at >= threshold]
        return before - len(self.turns)


class _FakeCharterRepo:
    def __init__(self) -> None:
        self.items: dict[str, ProjectCharter] = {}

    async def save(self, entity: ProjectCharter) -> None:
        self.items[entity.id] = entity

    async def get(self, entity_id: str) -> ProjectCharter | None:
        return self.items.get(entity_id)

    async def delete(self, entity_id: str) -> bool:
        return self.items.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[ProjectCharter, ...]:
        return tuple(self.items.values())[offset : offset + limit]

    async def query(
        self,
        filter_spec: CharterFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ProjectCharter, ...]:
        rows = [
            c
            for c in self.items.values()
            if (filter_spec.status is None or c.status is filter_spec.status)
            and (
                filter_spec.conversation_id is None
                or c.conversation_id == filter_spec.conversation_id
            )
            and (
                filter_spec.project_id is None or c.project_id == filter_spec.project_id
            )
            and (
                filter_spec.created_by is None or c.created_by == filter_spec.created_by
            )
        ]
        return tuple(rows[offset : offset + limit])

    async def count(self, filter_spec: CharterFilterSpec) -> int:
        return len(await self.query(filter_spec, limit=10_000))

    async def transition_if(
        self,
        entity_id: str,
        from_state: CharterStatus,
        to_state: CharterStatus,
        **updates: object,
    ) -> bool:
        current = self.items.get(entity_id)
        if current is None or current.status is not from_state:
            return False
        patch: dict[str, object] = {"status": to_state}
        for key in (
            "approved_at",
            "approved_by",
            "forecast_id",
            "correlation_id",
            "task_id",
        ):
            if key in updates:
                patch[key] = updates[key]
        self.items[entity_id] = current.model_copy(update=patch)
        return True


class _ScriptedStrategy:
    """Returns a queued sequence of interview decisions, one per turn."""

    def __init__(self, decisions: list[InterviewDecision]) -> None:
        self._decisions = decisions
        self.calls = 0

    async def run_turn(
        self,
        history: tuple[ConversationTurn, ...],
        *,
        project_id: NotBlankStr | None,
        currency: str,
    ) -> InterviewDecision:
        del history, project_id, currency
        decision = self._decisions[self.calls]
        self.calls += 1
        return decision


def _service(
    decisions: list[InterviewDecision],
    *,
    config: CharterConfig | None = None,
    clock: FakeClock | None = None,
) -> tuple[CharterInterviewService, _FakeCharterRepo]:
    charter_repo = _FakeCharterRepo()
    service = CharterInterviewService(
        strategy=_ScriptedStrategy(decisions),
        config=config or CharterConfig(),
        conversation_repo=_FakeConversationRepo(),
        turn_repo=_FakeTurnRepo(),
        charter_repo=charter_repo,
        clock=clock or FakeClock(start=_START),
    )
    return service, charter_repo


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
