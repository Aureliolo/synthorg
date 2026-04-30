"""Unit tests for :class:`ArtifactService`.

Verifies that the audit events emitted by the service are the new
``API_ARTIFACT_*`` constants (renamed from ``PERSISTENCE_ARTIFACT_*``
in #1562) and that mutations only emit when they actually mutate.
"""

import pytest
import structlog

from synthorg.core.artifact import Artifact
from synthorg.core.enums import ArtifactType
from synthorg.core.types import NotBlankStr
from synthorg.engine.artifacts.service import ArtifactService
from synthorg.observability.events.api import (
    API_ARTIFACT_CREATED,
    API_ARTIFACT_DELETED,
    API_ARTIFACT_UPDATED,
)

pytestmark = pytest.mark.unit


class _FakeArtifactRepo:
    """In-memory ArtifactRepository used as a test stub."""

    def __init__(self) -> None:
        self._rows: dict[str, Artifact] = {}

    async def save(self, artifact: Artifact) -> bool:
        created = artifact.id not in self._rows
        self._rows[artifact.id] = artifact
        return created

    async def get(self, artifact_id: NotBlankStr) -> Artifact | None:
        return self._rows.get(artifact_id)

    async def list_artifacts(
        self,
        *,
        task_id: NotBlankStr | None = None,
        created_by: NotBlankStr | None = None,
        artifact_type: ArtifactType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Artifact, ...]:
        # Mirror the real repo's validation so service-level tests
        # exercising bad pagination args see the same QueryError they
        # would in production.
        if limit < 1:
            from synthorg.core.persistence_errors import QueryError

            msg = f"limit must be >= 1, got {limit}"
            raise QueryError(msg)
        if offset < 0:
            from synthorg.core.persistence_errors import QueryError

            msg = f"offset must be >= 0, got {offset}"
            raise QueryError(msg)
        rows = sorted(self._rows.values(), key=lambda a: a.id)
        if task_id is not None:
            rows = [a for a in rows if a.task_id == task_id]
        if created_by is not None:
            rows = [a for a in rows if a.created_by == created_by]
        if artifact_type is not None:
            rows = [a for a in rows if a.type == artifact_type]
        return tuple(rows[offset : offset + limit])

    async def delete(self, artifact_id: NotBlankStr) -> bool:
        return self._rows.pop(artifact_id, None) is not None


async def test_create_emits_api_artifact_created() -> None:
    """``create`` emits ``API_ARTIFACT_CREATED`` (not ``PERSISTENCE_*``)."""
    repo = _FakeArtifactRepo()
    service = ArtifactService(repo=repo)

    with structlog.testing.capture_logs() as logs:
        result = await service.create(
            artifact_type=ArtifactType.CODE,
            path=NotBlankStr("path/to/file.py"),
            task_id=NotBlankStr("task-1"),
            created_by=NotBlankStr("agent-1"),
        )

    assert result.id.startswith("artifact-")
    # Persistence side-effect: catches a log-only impl that forgets
    # to actually call the repo.
    assert await repo.get(result.id) == result
    assert any(log["event"] == API_ARTIFACT_CREATED for log in logs), (
        f"expected {API_ARTIFACT_CREATED} in {logs}"
    )
    # Defend against re-introduction of the old persistence-layer event.
    assert not any(log["event"] == "persistence.artifact.saved" for log in logs), (
        "PERSISTENCE_ARTIFACT_SAVED should no longer fire"
    )


async def test_save_emits_api_artifact_updated_when_row_exists() -> None:
    """``save`` upsert emits ``API_ARTIFACT_UPDATED`` when row pre-exists."""
    repo = _FakeArtifactRepo()
    service = ArtifactService(repo=repo)
    artifact = Artifact(
        id=NotBlankStr("artifact-existing"),
        type=ArtifactType.CODE,
        path=NotBlankStr("path/to/file.py"),
        task_id=NotBlankStr("task-1"),
        created_by=NotBlankStr("agent-1"),
    )
    await repo.save(artifact)
    # Use a *different* artifact value on the second save so the
    # post-call repo state proves ``service.save`` reached the repo
    # (a log-only impl would leave the repo on the original value).
    updated = artifact.model_copy(
        update={"path": NotBlankStr("path/to/renamed.py")},
    )

    with structlog.testing.capture_logs() as logs:
        await service.save(updated)

    fetched = await repo.get(artifact.id)
    assert fetched is not None
    assert fetched.path == "path/to/renamed.py"
    assert any(log["event"] == API_ARTIFACT_UPDATED for log in logs)
    assert not any(log["event"] == API_ARTIFACT_CREATED for log in logs)
    assert not any(log["event"] == "persistence.artifact.saved" for log in logs)


async def test_save_emits_api_artifact_created_on_first_write() -> None:
    """``save`` upsert emits ``API_ARTIFACT_CREATED`` when no row pre-exists.

    Pins the create-vs-update audit contract so first-write upload
    paths do not show up as "phantom updates" in operator dashboards.
    """
    repo = _FakeArtifactRepo()
    service = ArtifactService(repo=repo)
    artifact = Artifact(
        id=NotBlankStr("artifact-new"),
        type=ArtifactType.CODE,
        path=NotBlankStr("path/to/new.py"),
        task_id=NotBlankStr("task-1"),
        created_by=NotBlankStr("agent-1"),
    )

    assert await repo.get(artifact.id) is None

    with structlog.testing.capture_logs() as logs:
        await service.save(artifact)

    # Persistence side-effect: catches a log-only impl that forgets
    # to call ``self._repo.save``.
    assert await repo.get(artifact.id) == artifact
    assert any(log["event"] == API_ARTIFACT_CREATED for log in logs)
    assert not any(log["event"] == API_ARTIFACT_UPDATED for log in logs)
    # Pin the first-write branch against the legacy persistence-layer
    # event (matches the update-path test below): repos are silent on
    # mutation; the API_ARTIFACT_* events are the canonical audit.
    assert not any(log["event"] == "persistence.artifact.saved" for log in logs)


async def test_delete_returns_true_and_emits_api_artifact_deleted() -> None:
    """Successful delete fires ``API_ARTIFACT_DELETED``."""
    repo = _FakeArtifactRepo()
    service = ArtifactService(repo=repo)
    artifact = Artifact(
        id=NotBlankStr("artifact-to-delete"),
        type=ArtifactType.CODE,
        path=NotBlankStr("doomed.py"),
        task_id=NotBlankStr("task-1"),
        created_by=NotBlankStr("agent-1"),
    )
    await repo.save(artifact)

    with structlog.testing.capture_logs() as logs:
        deleted = await service.delete(artifact.id)

    assert deleted is True
    # Persistence side-effect: the row really must be gone, not just
    # logged.  Catches a log-only impl that returns ``True`` without
    # touching the repo.
    assert await repo.get(artifact.id) is None
    assert any(log["event"] == API_ARTIFACT_DELETED for log in logs)
    assert not any(log["event"] == "persistence.artifact.deleted" for log in logs)


async def test_delete_missing_does_not_emit_event() -> None:
    """Missing artifact: ``delete`` returns ``False``, no audit fired."""
    repo = _FakeArtifactRepo()
    service = ArtifactService(repo=repo)

    with structlog.testing.capture_logs() as logs:
        deleted = await service.delete(NotBlankStr("artifact-missing"))

    assert deleted is False
    assert not any(log["event"] == API_ARTIFACT_DELETED for log in logs)
