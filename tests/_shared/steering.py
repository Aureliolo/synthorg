"""Shared steering test doubles.

``FakeBrainService`` appends entries straight into a
:class:`tests.unit.api.fakes.FakeProjectBrainRepository` so steering tests can
issue directives without the full memory-gated brain composition. The
``SteeringService`` write path only calls ``append_entry`` and then reads the
repo back, so this thin double is sufficient for unit, e2e, and acceptance
coverage of the steering mechanism.
"""

from datetime import UTC, datetime
from uuid import uuid4

from synthorg.core.clock import Clock
from synthorg.core.types import NotBlankStr
from synthorg.project_brain.models import BrainEntry, BrainEntryStatus
from tests.unit.api.fakes import FakeProjectBrainRepository

_DEFAULT_RECORDED_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class FakeBrainService:
    """Append-only fake mirroring ``ProjectBrainService.append_entry``."""

    def __init__(
        self,
        repo: FakeProjectBrainRepository,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._repo = repo
        self._clock = clock

    async def append_entry(  # noqa: PLR0913 -- mirrors the real signature
        self,
        *,
        project_id: NotBlankStr,
        title: NotBlankStr,
        rationale: NotBlankStr,
        status: BrainEntryStatus,
        author: NotBlankStr,
        payload: object,
        related_task_ids: tuple[NotBlankStr, ...] = (),
        tags: tuple[NotBlankStr, ...] = (),
    ) -> BrainEntry:
        """Construct a brain entry and append it with the next revision.

        Returns:
            The stored :class:`BrainEntry` (revision stamped by the repo).
        """
        recorded_at = (
            self._clock.now() if self._clock is not None else _DEFAULT_RECORDED_AT
        )
        entry = BrainEntry(
            entry_id=NotBlankStr(str(uuid4())),
            revision=1,
            project_id=project_id,
            entry_kind=payload.entry_kind,  # type: ignore[attr-defined]
            title=title,
            rationale=rationale,
            status=status,
            author=author,
            recorded_at=recorded_at,
            related_task_ids=related_task_ids,
            tags=tags,
            payload=payload,  # type: ignore[arg-type]
        )
        return await self._repo.append_with_next_revision(entry)
