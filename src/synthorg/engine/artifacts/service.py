"""Artifact service layer.

Wraps :class:`ArtifactRepository` so API controllers can list / get /
save / delete artifacts without reaching into
``state.app_state.persistence.artifacts`` directly. Centralises the
``API_ARTIFACT_*`` logging so every mutation has the same audit shape.

When constructed with the optional ``storage`` dependency the service
also orchestrates the storage-content delete inside
:meth:`delete_with_content`, so controllers stay out of the
storage / persistence error-taxonomy mix.  Upload / download paths
remain controller-side because they involve streaming bytes and rely
on Litestar request / response abstractions; the delete path is the
one mixed-orchestration site that benefits from the service boundary.
"""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.core.artifact import Artifact, ArtifactType
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ArtifactPersistenceNoStorageError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_ARTIFACT_CREATED,
    API_ARTIFACT_DELETED,
    API_ARTIFACT_UPDATED,
)
from synthorg.observability.events.persistence.artifact import (
    PERSISTENCE_ARTIFACT_DELETE_FAILED,
    PERSISTENCE_ARTIFACT_DELETE_NO_STORAGE,
)
from synthorg.observability.events.persistence.artifact_storage import (
    PERSISTENCE_ARTIFACT_STORAGE_DELETE_FAILED,
)

if TYPE_CHECKING:
    from synthorg.persistence.artifact_storage import ArtifactStorageBackend

from synthorg.persistence.artifact_protocol import (
    ArtifactFilterSpec,
    ArtifactRepository,
)

logger = get_logger(__name__)


class ArtifactService:
    """CRUD orchestration for artifacts with uniform audit logging."""

    __slots__ = ("_repo", "_storage")

    def __init__(
        self,
        *,
        repo: ArtifactRepository,
        storage: ArtifactStorageBackend | None = None,
    ) -> None:
        self._repo = repo
        self._storage = storage

    async def list_artifacts(
        self,
        *,
        task_id: NotBlankStr | None = None,
        created_by: NotBlankStr | None = None,
        artifact_type: ArtifactType | None = None,
    ) -> tuple[Artifact, ...]:
        """List artifacts, optionally filtered by one or more facets.

        All three filters are AND-combined when provided; passing
        ``None`` for a filter omits it from the query.

        Returns:
            Tuple of matching artifacts ordered by the repository's
            default ordering.
        """
        return await self._repo.query(
            ArtifactFilterSpec(
                task_id=task_id,
                created_by=created_by,
                artifact_type=artifact_type,
            ),
        )

    async def get(self, artifact_id: NotBlankStr) -> Artifact | None:
        """Return a single artifact by id, or ``None`` when missing."""
        return await self._repo.get(artifact_id)

    async def create(  # noqa: PLR0913
        self,
        *,
        artifact_type: ArtifactType,
        path: NotBlankStr,
        task_id: NotBlankStr,
        created_by: NotBlankStr,
        description: str = "",
        content_type: str = "",
        project_id: NotBlankStr | None = None,
    ) -> Artifact:
        """Persist a new artifact with a server-generated id.

        The id has the shape ``"artifact-<uuid4-hex>"`` (full 32 hex
        chars, 128 bits of entropy) and the ``created_at`` timestamp is
        set to the current UTC time; callers do not provide either.
        Truncating the UUID shrinks entropy enough for collisions to
        become a real risk at scale, so the full hex is retained.

        Returns:
            The newly-constructed :class:`Artifact` after persistence
            (caller-supplied fields plus the generated id and UTC
            timestamp).
        """
        artifact = Artifact(
            id=NotBlankStr(f"artifact-{uuid.uuid4().hex}"),
            type=artifact_type,
            path=path,
            task_id=task_id,
            created_by=created_by,
            description=description,
            content_type=content_type,
            project_id=project_id,
            created_at=datetime.now(UTC),
        )
        created = await self._repo.save_returning_outcome(artifact)
        logger.info(
            API_ARTIFACT_CREATED if created else API_ARTIFACT_UPDATED,
            artifact_id=artifact.id,
        )
        return artifact

    async def save(self, artifact: Artifact) -> None:
        """Upsert a caller-constructed artifact (used by content upload).

        ``save()`` is an upsert -- the same path is taken whether the
        row exists or not.  The repository performs the create-vs-
        update decision atomically (SQLite: ``INSERT OR IGNORE`` +
        conditional ``UPDATE``; Postgres: ``ON CONFLICT ... RETURNING
        (xmax = 0)``) and returns the lifecycle outcome.  This avoids
        the TOCTOU window of a separate ``get()`` probe -- concurrent
        writers can no longer both observe "missing" and both report
        ``API_ARTIFACT_CREATED``.  Operators keying alerts on
        ``API_ARTIFACT_UPDATED`` see neither phantom updates on
        first-write upload flows nor phantom creates on collisions.
        """
        created = await self._repo.save_returning_outcome(artifact)
        logger.info(
            API_ARTIFACT_CREATED if created else API_ARTIFACT_UPDATED,
            artifact_id=artifact.id,
        )

    async def delete(self, artifact_id: NotBlankStr) -> bool:
        """Delete an artifact; returns ``True`` when a row was removed.

        Returns:
            ``True`` when the repository deleted a matching row;
            ``False`` when no row matched ``artifact_id``.
        """
        deleted = await self._repo.delete(artifact_id)
        if deleted:
            logger.info(
                API_ARTIFACT_DELETED,
                artifact_id=artifact_id,
            )
        return deleted

    async def delete_with_content(self, artifact_id: NotBlankStr) -> bool:
        """Delete the artifact's storage blob THEN its metadata row.

        Storage-first ordering preserves the metadata-first invariant
        on failure: if the blob delete raises, the row remains so the
        inconsistency is detectable; the reverse order would leave an
        orphan blob with no metadata pointing at it.

        Storage failures route through
        ``PERSISTENCE_ARTIFACT_STORAGE_DELETE_FAILED`` and propagate;
        the persistence delete only runs after the blob is gone.

        Raises:
            ArtifactPersistenceNoStorageError: If the service was
                constructed without a ``storage`` dependency
                (controller helper bug, surfaces 500 with RFC 9457
                metadata via the central exception handler).
            Exception: Any storage-backend or metadata-delete
                failure propagates with type intact.

        Returns:
            ``True`` if either the blob or the metadata row was
            successfully removed; ``False`` only when both were
            already absent.
        """
        if self._storage is None:
            msg = (
                "ArtifactService.delete_with_content called but the "
                "service was constructed without a storage backend"
            )
            # Log before raising so the error path leaves a structured
            # breadcrumb for operators (the controller helper that
            # forgot to pass ``storage=`` is the typical cause).
            logger.error(
                PERSISTENCE_ARTIFACT_DELETE_NO_STORAGE,
                artifact_id=artifact_id,
                reason=msg,
            )
            raise ArtifactPersistenceNoStorageError(msg)
        try:
            blob_deleted = await self._storage.delete(artifact_id)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                PERSISTENCE_ARTIFACT_STORAGE_DELETE_FAILED,
                artifact_id=artifact_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        # The metadata delete is the highest-risk branch: a failure
        # here leaves the blob gone but the row present, so log with
        # context before re-raising so operators can reconcile.
        try:
            metadata_deleted = await self.delete(artifact_id)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                PERSISTENCE_ARTIFACT_DELETE_FAILED,
                artifact_id=artifact_id,
                note="metadata_delete_failed_after_blob_deleted",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        # ``True`` if either side actually removed something so callers
        # can distinguish "nothing to delete" from "deleted at least one
        # of the blob / metadata pair".
        return blob_deleted or metadata_deleted
