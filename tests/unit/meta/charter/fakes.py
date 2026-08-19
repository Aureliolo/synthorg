"""Shared in-memory doubles for the charter interview tests."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from synthorg.core.types import NotBlankStr
from synthorg.meta.charter.config import CharterConfig
from synthorg.meta.charter.enums import CharterStatus
from synthorg.meta.charter.models import (
    BudgetEnvelope,
    CharterDraft,
    InterviewDecision,
    ProjectCharter,
)
from synthorg.meta.charter.service import CharterInterviewService
from synthorg.meta.chief_of_staff.models import ConversationTurn
from synthorg.persistence.charter_protocol import CharterFilterSpec
from tests._shared import FakeClock
from tests._shared.conversation_fakes import (
    FakeConversationRepo,
    FakeTurnRepo,
)

START = datetime(2026, 5, 22, 9, 0, 0, tzinfo=UTC)


def draft(**overrides: object) -> CharterDraft:
    """Build a charter draft, overriding any field.

    Returns:
        ``CharterDraft`` instance.
    """
    defaults: dict[str, object] = {
        "title": "Memory layer",
        "brief": "Build a better memory layer.",
        "success_criteria": (NotBlankStr("recall +10%"),),
        "envelope": BudgetEnvelope(amount=5000.0, currency="USD"),
        "proposed_project_name": "memory-layer",
        "assumed_facets": (),
    }
    defaults.update(overrides)
    return CharterDraft(**defaults)  # type: ignore[arg-type]


class FakeCharterRepo:
    """In-memory ``CharterRepository``."""

    def __init__(self) -> None:
        self.items: dict[str, ProjectCharter] = {}

    async def save(self, entity: ProjectCharter) -> None:
        self.items[entity.id] = entity

    async def get(self, entity_id: str) -> ProjectCharter | None:
        """Return the charter with *entity_id*.

        Returns:
            The charter, or ``None`` when unknown.
        """
        return self.items.get(entity_id)

    async def delete(self, entity_id: str) -> bool:
        """Delete the charter with *entity_id*.

        Returns:
            Whether a row was removed.
        """
        return self.items.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[ProjectCharter, ...]:
        """Page every charter.

        Returns:
            The requested page.
        """
        return tuple(self.items.values())[offset : offset + limit]

    async def query(
        self,
        filter_spec: CharterFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ProjectCharter, ...]:
        """Page the charters matching *filter_spec*.

        Returns:
            The requested page.
        """
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
        """Count the charters matching *filter_spec*.

        Returns:
            The number of matching rows.
        """
        return len(await self.query(filter_spec, limit=10_000))

    async def transition_if(
        self,
        entity_id: str,
        from_state: CharterStatus,
        to_state: CharterStatus,
        **updates: object,
    ) -> bool:
        """Move a charter between statuses, if it is still in *from_state*.

        Returns:
            Whether the transition applied.
        """
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

    async def save_edit_if_version(
        self,
        entity: ProjectCharter,
        *,
        expected_version: int,
    ) -> bool:
        """Write an edit, if the row is still at *expected_version*.

        Returns:
            Whether the edit applied.
        """
        current = self.items.get(entity.id)
        if (
            current is None
            or current.version != expected_version
            or current.status is not CharterStatus.DRAFTED
        ):
            return False
        self.items[entity.id] = entity
        return True


class ScriptedStrategy:
    """Returns a queued sequence of interview decisions, one per turn."""

    def __init__(self, decisions: list[InterviewDecision]) -> None:
        self._decisions = decisions
        self.calls = 0
        self.configs: list[CharterConfig] = []

    async def run_turn(
        self,
        history: tuple[ConversationTurn, ...],
        *,
        project_id: NotBlankStr | None,
        config: CharterConfig,
    ) -> InterviewDecision:
        """Return the next queued decision.

        Returns:
            ``InterviewDecision`` instance.
        """
        del history, project_id
        self.configs.append(config)
        decision = self._decisions[self.calls]
        self.calls += 1
        return decision


def service(
    decisions: list[InterviewDecision],
    *,
    config: CharterConfig | None = None,
    clock: FakeClock | None = None,
    config_provider: Callable[[], Awaitable[CharterConfig]] | None = None,
) -> tuple[CharterInterviewService, FakeCharterRepo]:
    """Build an interview service over in-memory stores.

    Returns:
        The service and the charter repository behind it.
    """
    charter_repo = FakeCharterRepo()
    built = CharterInterviewService(
        strategy=ScriptedStrategy(decisions),
        config=config or CharterConfig(),
        conversation_repo=FakeConversationRepo(),
        turn_repo=FakeTurnRepo(),
        charter_repo=charter_repo,
        clock=clock or FakeClock(start=START),
        config_provider=config_provider,
    )
    return built, charter_repo


__all__ = [
    "START",
    "FakeCharterRepo",
    "ScriptedStrategy",
    "draft",
    "service",
]
