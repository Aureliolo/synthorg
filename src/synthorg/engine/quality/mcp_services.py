# ruff: noqa: D102, EM101, PLR0913
"""Quality facades for the MCP handler layer.

Three facades: QualityFacadeService wraps the performance tracker's
scoring surface; ReviewFacadeService is an in-process review queue;
EvaluationVersionService surfaces evaluation-config version history.
"""

import asyncio
import copy
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.quality import (
    REVIEW_CREATED_VIA_MCP,
    REVIEW_UPDATED_VIA_MCP,
)

if TYPE_CHECKING:
    # Concrete tracker faked in tests; a runtime import would make typeguard
    # enforce a nominal isinstance the fake cannot satisfy.
    from synthorg.hr.performance.tracker import PerformanceTracker

logger = get_logger(__name__)


# ── QualityFacadeService ──────────────────────────────────────────


class QualityFacadeService:
    """Wraps :class:`PerformanceTracker`'s scoring surface."""

    def __init__(self, *, tracker: PerformanceTracker) -> None:
        self._tracker = tracker

    async def get_summary(self) -> Mapping[str, object]:
        fn = getattr(self._tracker, "get_quality_summary", None)
        if callable(fn):
            result = fn()
            if hasattr(result, "__await__"):
                result = await result
            return dict(result)
        raise CapabilityNotSupportedError(
            "quality_summary",
            "PerformanceTracker does not expose get_quality_summary",
        )

    async def get_agent_quality(
        self,
        agent_id: NotBlankStr,
    ) -> Mapping[str, object]:
        fn = getattr(self._tracker, "get_snapshot", None)
        if callable(fn):
            snapshot = fn(agent_id)
            if hasattr(snapshot, "__await__"):
                snapshot = await snapshot
            dump_fn = getattr(snapshot, "model_dump", None)
            if callable(dump_fn):
                return cast("Mapping[str, object]", dump_fn(mode="json"))
            return dict(snapshot.__dict__) if snapshot else {}
        raise CapabilityNotSupportedError(
            "quality_agent",
            "PerformanceTracker does not expose get_snapshot",
        )

    async def list_scores(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[tuple[object, ...], int]:
        """Return paginated quality scores + unfiltered total.

        Args:
            agent_id: Optional agent identifier filter.
            offset: Non-negative page offset.
            limit: Optional positive page size; ``None`` returns every
                score from ``offset`` onwards.

        Raises:
            ValueError: If ``offset`` is negative, or ``limit`` is
                provided and non-positive.
            CapabilityNotSupportedError: If the underlying tracker
                does not expose ``list_quality_scores``.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            raise ValueError(msg)
        if limit is not None and limit < 1:
            msg = f"limit must be >= 1 when provided, got {limit}"
            raise ValueError(msg)
        fn = getattr(self._tracker, "list_quality_scores", None)
        if callable(fn):
            result = fn(agent_id) if agent_id else fn()
            if hasattr(result, "__await__"):
                result = await result
            items = tuple(result)
            total = len(items)
            end = total if limit is None else offset + limit
            return items[offset:end], total
        raise CapabilityNotSupportedError(
            "quality_scores",
            "PerformanceTracker does not expose list_quality_scores",
        )


# ── ReviewFacadeService ───────────────────────────────────────────


class _ReviewRecord:
    __slots__ = (
        "comments",
        "created_at",
        "id",
        "reviewer_id",
        "task_id",
        "updated_at",
        "verdict",
    )

    def __init__(
        self,
        *,
        id: UUID,  # noqa: A002
        task_id: str,
        reviewer_id: str,
        verdict: str,
        comments: str | None,
        created_at: datetime,
    ) -> None:
        self.id = id
        self.task_id = task_id
        self.reviewer_id = reviewer_id
        self.verdict = verdict
        self.comments = comments
        self.created_at = created_at
        self.updated_at = created_at

    def to_dict(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "task_id": self.task_id,
            "reviewer_id": self.reviewer_id,
            "verdict": self.verdict,
            "comments": self.comments,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class ReviewFacadeService:
    """In-process review-queue facade.

    Mutations are serialised through a single :class:`asyncio.Lock` so
    concurrent MCP handler calls cannot race on the in-memory dict
    (check-then-act in :meth:`update_review`).  Records are deep-copied
    on every return so callers cannot mutate the canonical instance
    and bypass the audit-logged update path.
    """

    def __init__(self) -> None:
        self._reviews: dict[UUID, _ReviewRecord] = {}
        self._lock = asyncio.Lock()

    async def list_reviews(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[tuple[_ReviewRecord, ...], int]:
        """Return paginated reviews newest-first plus the unfiltered total.

        Args:
            offset: Non-negative page offset.
            limit: Optional positive page size; ``None`` returns every
                review from ``offset`` onwards.

        Raises:
            ValueError: If ``offset`` is negative, or ``limit`` is
                provided and non-positive.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            raise ValueError(msg)
        if limit is not None and limit < 1:
            msg = f"limit must be >= 1 when provided, got {limit}"
            raise ValueError(msg)
        async with self._lock:
            snapshot = tuple(copy.deepcopy(r) for r in self._reviews.values())
        ordered = tuple(
            sorted(snapshot, key=lambda r: r.created_at, reverse=True),
        )
        total = len(ordered)
        end = total if limit is None else offset + limit
        return ordered[offset:end], total

    async def get_review(self, review_id: NotBlankStr) -> _ReviewRecord | None:
        try:
            key = UUID(review_id)
        except ValueError:
            return None
        async with self._lock:
            record = self._reviews.get(key)
            return copy.deepcopy(record) if record is not None else None

    async def create_review(
        self,
        *,
        task_id: NotBlankStr,
        reviewer_id: NotBlankStr,
        verdict: NotBlankStr,
        comments: str | None = None,
    ) -> _ReviewRecord:
        record = _ReviewRecord(
            id=uuid4(),
            task_id=task_id,
            reviewer_id=reviewer_id,
            verdict=verdict,
            comments=comments,
            created_at=datetime.now(UTC),
        )
        async with self._lock:
            self._reviews[record.id] = record
        logger.info(
            REVIEW_CREATED_VIA_MCP,
            review_id=str(record.id),
            task_id=task_id,
            verdict=verdict,
        )
        return copy.deepcopy(record)

    async def update_review(
        self,
        *,
        review_id: NotBlankStr,
        verdict: NotBlankStr | None = None,
        comments: str | None = None,
        actor_id: NotBlankStr,
    ) -> _ReviewRecord | None:
        try:
            key = UUID(review_id)
        except ValueError:
            return None
        async with self._lock:
            record = self._reviews.get(key)
            if record is None:
                return None
            if verdict is not None:
                record.verdict = verdict
            if comments is not None:
                record.comments = comments
            record.updated_at = datetime.now(UTC)
            returned = copy.deepcopy(record)
        logger.info(
            REVIEW_UPDATED_VIA_MCP,
            review_id=review_id,
            actor_id=actor_id,
        )
        return returned


# ── EvaluationVersionService ─────────────────────────────────────


class EvaluationVersionService:
    """Evaluation-config version history facade.

    Wraps :class:`PersistenceBackend.evaluation_config_versions` when
    available; returns ``()`` / ``None`` otherwise.
    """

    def __init__(self, *, persistence: object) -> None:
        self._persistence = persistence

    async def list_versions(self) -> Sequence[object]:
        repo = getattr(self._persistence, "evaluation_config_versions", None)
        if repo is None:
            raise CapabilityNotSupportedError(
                "evaluation_versions_list",
                "persistence backend does not expose evaluation_config_versions",
            )
        fn = getattr(repo, "list_versions", None)
        if not callable(fn):
            raise CapabilityNotSupportedError(
                "evaluation_versions_list",
                "evaluation_config_versions repository does not expose list_versions",
            )
        return tuple(await fn())

    async def get_version(
        self,
        version_id: NotBlankStr,
    ) -> object | None:
        repo = getattr(self._persistence, "evaluation_config_versions", None)
        if repo is None:
            raise CapabilityNotSupportedError(
                "evaluation_versions_get",
                "persistence backend does not expose evaluation_config_versions",
            )
        fn = getattr(repo, "get_version", None)
        if not callable(fn):
            raise CapabilityNotSupportedError(
                "evaluation_versions_get",
                "evaluation_config_versions repository does not expose get_version",
            )
        return cast("object | None", await fn(version_id))


__all__ = [
    "EvaluationVersionService",
    "QualityFacadeService",
    "ReviewFacadeService",
]
