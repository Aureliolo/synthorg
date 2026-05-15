"""Ceremony scheduler state persistence protocol and record.

The ``CeremonyScheduler`` owns four in-memory state attributes that
together describe the ceremony-trigger position of one active sprint:

* ``_completion_counters``: ceremony-name to completion count.
* ``_fired_once_triggers``: trigger names that have already fired.
* ``_total_completions``: number of task completions across the sprint.
* ``_velocity_history``: recent ``VelocityRecord`` snapshots.

Before WP-1 these were lost on process restart. This repository
persists all four as a single snapshot row per sprint, written
atomically under the scheduler's lock after every mutation and read
back at ``activate_sprint`` time. JSON-blob columns carry the dict,
set, and tuple fields; ``total_completions`` is a plain integer.

The repo is a singleton-per-sprint: the natural key is ``sprint_id``
(string), and there is exactly one row per active sprint. Old sprint
rows can be deleted via ``delete`` once the sprint terminates, but the
scheduler keeps them for late-arriving completions until explicit
cleanup -- the table is small (one row per sprint, ever).
"""

from datetime import datetime  # noqa: TC003 -- runtime needed by Pydantic field
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import IdKeyedRepository


class CeremonySchedulerStateRecord(BaseModel):
    """Persisted snapshot of one ceremony scheduler's per-sprint state.

    Attributes:
        sprint_id: Sprint whose state this snapshot belongs to.
        completion_counters_json: JSON object mapping ceremony name to
            completion count (``dict[str, int]``).
        fired_once_triggers_json: JSON array of trigger names that have
            already fired one-shot ceremonies (``list[str]``).
        total_completions: Number of task completions counted across
            the entire sprint.
        velocity_history_json: JSON array of velocity-record snapshots
            (each record is the JSON-mode dump of a ``VelocityRecord``).
        updated_at: Caller-supplied UTC timestamp; the repo just
            persists it. Mirrors the ``PresetOverride.updated_at``
            convention.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    sprint_id: NotBlankStr = Field(description="Sprint whose state is persisted")
    completion_counters_json: str = Field(
        description="JSON object of ceremony_name to count"
    )
    fired_once_triggers_json: str = Field(
        description="JSON array of fired-once trigger names"
    )
    total_completions: int = Field(ge=0, description="Total completions in this sprint")
    velocity_history_json: str = Field(
        description="JSON array of VelocityRecord JSON-mode dumps"
    )
    updated_at: datetime = Field(description="UTC timestamp of this snapshot")


@runtime_checkable
class CeremonySchedulerStateRepository(
    IdKeyedRepository[CeremonySchedulerStateRecord, NotBlankStr],
    Protocol,
):
    """Persistence interface for ceremony scheduler state snapshots.

    Composes :class:`IdKeyedRepository` (ADR-0001): the natural key is
    ``sprint_id``. Implementations live under ``persistence/sqlite/``
    and ``persistence/postgres/`` with a shared dual-backend
    conformance suite under ``tests/conformance/persistence/``.
    """

    async def save(self, entity: CeremonySchedulerStateRecord) -> None:
        """Persist a state snapshot for one sprint (upsert by sprint_id).

        Args:
            entity: Snapshot to persist. Replaces any existing row with
                the same ``sprint_id``.

        Raises:
            QueryError: If the underlying write fails.
        """
        ...

    async def get(self, entity_id: NotBlankStr) -> CeremonySchedulerStateRecord | None:
        """Load the snapshot for one sprint, or ``None`` if absent.

        Args:
            entity_id: Sprint id whose state to load.

        Returns:
            The persisted snapshot, or ``None`` if no row exists.

        Raises:
            QueryError: If the underlying read fails.
        """
        ...

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete the snapshot for one sprint.

        Args:
            entity_id: Sprint id whose state to remove.

        Returns:
            ``True`` if a row was deleted, ``False`` if no row existed.

        Raises:
            QueryError: If the underlying DELETE fails.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[CeremonySchedulerStateRecord, ...]:
        """List persisted snapshots, ordered by ``sprint_id`` ascending.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Paginated snapshots in ascending sprint-id order.

        Raises:
            QueryError: If the underlying read fails.
        """
        ...
