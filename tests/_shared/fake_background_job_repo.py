"""One in-memory double satisfying ``BackgroundJobRepository``.

Three suites had grown their own copy, one of them with a status set
frozen at ``{PENDING, RUNNING}`` instead of the shared
``LIVE_BACKGROUND_JOB_STATUSES`` constant -- exactly the drift a single
double exists to prevent (see ``fake_sandbox.py``'s own docstring for
the same rationale on a different double).
"""

from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence.background_job_protocol import (
    LIVE_BACKGROUND_JOB_STATUSES,
    BackgroundJobRecord,
    BackgroundJobStatus,
)

__all__ = ["InMemoryBackgroundJobRepository"]


class InMemoryBackgroundJobRepository:
    """Minimal in-memory double satisfying ``BackgroundJobRepository``."""

    def __init__(self) -> None:
        self._rows: dict[str, BackgroundJobRecord] = {}

    async def save(self, entity: BackgroundJobRecord, /) -> None:
        self._rows[entity.job_id] = entity

    async def save_if_live(self, entity: BackgroundJobRecord, /) -> bool:
        current = self._rows.get(entity.job_id)
        if current is None or current.status not in LIVE_BACKGROUND_JOB_STATUSES:
            return False
        self._rows[entity.job_id] = entity
        return True

    async def get(self, entity_id: str, /) -> BackgroundJobRecord | None:
        return self._rows.get(entity_id)

    async def delete(self, entity_id: str, /) -> bool:
        return self._rows.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> tuple[BackgroundJobRecord, ...]:
        ordered = sorted(self._rows.values(), key=lambda r: r.job_id)
        return tuple(ordered[offset : offset + limit])

    async def load_all(self) -> tuple[BackgroundJobRecord, ...]:
        return tuple(self._rows.values())

    async def list_by_container(
        self,
        container_id: str,
        *,
        statuses: frozenset[BackgroundJobStatus] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[BackgroundJobRecord, ...]:
        matches = [
            r
            for r in self._rows.values()
            if r.container_id == container_id
            and (statuses is None or r.status in statuses)
        ]
        return tuple(matches[offset : offset + limit])

    async def count_live_by_owner(self, owner_id: str) -> int:
        return sum(
            1
            for r in self._rows.values()
            if r.owner_id == owner_id and r.status in LIVE_BACKGROUND_JOB_STATUSES
        )

    async def list_by_owner(
        self, owner_id: str, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> tuple[BackgroundJobRecord, ...]:
        matches = [r for r in self._rows.values() if r.owner_id == owner_id]
        return tuple(matches[offset : offset + limit])
