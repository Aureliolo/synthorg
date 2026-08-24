"""Shared in-memory ``SprintRepository`` for the sprint suites.

One fake, not one per suite. The three sprint suites (service, tail,
recovery) each drive the same repository through the same guards, and three
copies of it is three chances for one of them to accept a write the database
refuses, which is exactly how a suite stays green over an invariant it is
meant to be exercising.

It models the database's CONSTRAINTS rather than only its methods: the
partial unique index admitting one non-completed sprint per scope, the
compare-and-set on the lifecycle status, and the two guarded backlog writes
including the way each re-derives its story-point total from
``task_points`` instead of accumulating.
"""

from synthorg.core.persistence_errors import ConstraintViolationError
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.sprint_lifecycle import (
    OPEN_SPRINT_STATUSES,
    Sprint,
    SprintStatus,
)
from synthorg.persistence.sprint_protocol import SprintFilterSpec

_DEFAULT_PAGE = 50
_COUNT_PAGE = 1_000_000


class FakeSprintRepository:
    """In-memory sprint store that refuses what the real one refuses."""

    def __init__(self, *sprints: Sprint) -> None:
        self._rows: dict[str, Sprint] = {s.id: s for s in sprints}

    @property
    def rows(self) -> dict[str, Sprint]:
        """The stored sprints, for assertions.

        Returns:
            The live mapping, keyed by sprint id.
        """
        return self._rows

    @staticmethod
    def _scope_key(sprint: Sprint) -> str:
        """Mirror the index's ``COALESCE(project, '')`` key.

        Returns:
            The scope the sprint occupies while it is not completed.
        """
        return sprint.project or ""

    async def save(self, entity: Sprint) -> None:
        """Upsert, refusing a second open sprint in one scope.

        Raises:
            ConstraintViolationError: When the scope already holds a
                different non-completed sprint, as the partial unique
                index does.
        """
        if entity.status is not SprintStatus.COMPLETED:
            key = self._scope_key(entity)
            for existing in self._rows.values():
                if (
                    existing.id != entity.id
                    and existing.status is not SprintStatus.COMPLETED
                    and self._scope_key(existing) == key
                ):
                    msg = f"scope {key!r} already has open sprint {existing.id!r}"
                    raise ConstraintViolationError(msg, constraint=msg)
        self._rows[entity.id] = entity

    async def get(self, entity_id: str) -> Sprint | None:
        """Return the stored sprint, or ``None``.

        Returns:
            The matching sprint.
        """
        return self._rows.get(entity_id)

    async def delete(self, entity_id: str) -> bool:
        """Drop a sprint.

        Returns:
            ``True`` when a row existed.
        """
        return self._rows.pop(entity_id, None) is not None

    @staticmethod
    def _sorted(rows: list[Sprint]) -> list[Sprint]:
        """Order rows as the repositories do.

        Returns:
            The rows, newest-first.
        """
        return sorted(rows, key=lambda s: (s.sprint_number, s.id), reverse=True)

    async def list_items(
        self, *, limit: int = _DEFAULT_PAGE, offset: int = 0
    ) -> tuple[Sprint, ...]:
        """Page every sprint, newest-first.

        Returns:
            One page of sprints.
        """
        rows = self._sorted(list(self._rows.values()))
        return tuple(rows[offset : offset + limit])

    async def query(
        self,
        filter_spec: SprintFilterSpec,
        *,
        limit: int = _DEFAULT_PAGE,
        offset: int = 0,
    ) -> tuple[Sprint, ...]:
        """Page the sprints matching *filter_spec*, newest-first.

        Returns:
            One page of matching sprints.
        """
        rows = [
            s
            for s in self._rows.values()
            if (filter_spec.project is None or s.project == filter_spec.project)
            and (not filter_spec.org_wide_only or s.project is None)
            and (filter_spec.status is None or s.status is filter_spec.status)
        ]
        return tuple(self._sorted(rows)[offset : offset + limit])

    async def count(self, filter_spec: SprintFilterSpec) -> int:
        """Count the sprints matching *filter_spec*.

        Returns:
            The number of matching rows.
        """
        return len(await self.query(filter_spec, limit=_COUNT_PAGE))

    async def transition_if(
        self,
        entity_id: str,
        from_state: SprintStatus,
        to_state: SprintStatus,
        **updates: object,
    ) -> bool:
        """Compare-and-set the lifecycle status.

        Returns:
            ``True`` when the row was in *from_state* and moved.
        """
        row = self._rows.get(entity_id)
        if row is None or row.status is not from_state:
            return False
        overrides = {k: v for k, v in updates.items() if v is not None}
        self._rows[entity_id] = row.model_copy(update={"status": to_state, **overrides})
        return True

    async def complete_task_if(self, sprint_id: str, task_id: str) -> Sprint | None:
        """Append *task_id* iff the same guard the SQL applies holds.

        Returns:
            The post-image, or ``None`` when the guard did not match.
        """
        row = self._rows.get(sprint_id)
        if row is None or row.status not in OPEN_SPRINT_STATUSES:
            return None
        if task_id not in row.task_ids or task_id in row.completed_task_ids:
            return None
        completed = (*row.completed_task_ids, task_id)
        updated = row.model_copy(
            update={
                "completed_task_ids": completed,
                # Re-derived over the resulting set and clamped, as both
                # backends do, so a suite written against this fake cannot
                # pass on an accumulation the real statements never perform.
                "story_points_completed": min(
                    row.story_points_committed,
                    sum(row.task_points.get(t, 0.0) for t in completed),
                ),
            }
        )
        self._rows[sprint_id] = updated
        return updated

    async def add_task_if_planning(
        self, sprint_id: str, task_id: str, story_points: float
    ) -> Sprint | None:
        """Append *task_id* to the backlog iff the sprint is still PLANNING.

        Returns:
            The post-image, or ``None`` when the guard did not match.
        """
        row = self._rows.get(sprint_id)
        if row is None or row.status is not SprintStatus.PLANNING:
            return None
        if task_id in row.task_ids:
            return None
        points = {**row.task_points, task_id: story_points}
        updated = row.model_copy(
            update={
                "task_ids": (*row.task_ids, NotBlankStr(task_id)),
                "task_points": points,
                "story_points_committed": sum(points.values()),
            }
        )
        self._rows[sprint_id] = updated
        return updated


__all__ = ["FakeSprintRepository"]
