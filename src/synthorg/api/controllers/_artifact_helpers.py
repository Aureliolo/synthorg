"""Dependency construction and storage invariants for the artifact controller."""

from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.core.artifact import Artifact
from synthorg.engine.artifacts.service import ArtifactService
from synthorg.engine.workspace.state import WorkspaceStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.artifact import (
    PERSISTENCE_ARTIFACT_SAVE_FAILED,
)
from synthorg.observability.events.persistence.artifact_storage import (
    PERSISTENCE_ARTIFACT_STORAGE_ROLLBACK_FAILED,
)
from synthorg.persistence.artifact_storage import ArtifactStorageBackend
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)

SAFE_CONTENT_TYPES = frozenset(
    {
        "application/octet-stream",
        "application/json",
        "application/pdf",
        "application/xml",
        "application/zip",
        "application/gzip",
        "application/x-tar",
        "image/png",
        "image/jpeg",
        "image/gif",
        # image/svg+xml intentionally excluded -- SVG is an XML document
        # with full JavaScript execution capability (XSS risk).
        "image/webp",
        "text/plain",
        "text/csv",
        "text/xml",
        "text/markdown",
    }
)


def artifact_storage(state: State) -> ArtifactStorageBackend:
    """Resolve the artifact content storage backend.

    Args:
        state: Application state.

    Returns:
        The configured ``ArtifactStorageBackend``.
    """
    return require_service(
        state.app_state.slice(WorkspaceStateSlice).artifact_storage,
        "Artifact Storage",
    )


def artifact_service(state: State) -> ArtifactService:
    """Build the per-request :class:`ArtifactService` instance.

    Both the persistence repo and the artifact storage backend are
    plumbed in so the service can orchestrate
    :meth:`ArtifactService.delete_with_content` (storage delete +
    persistence delete with the right ordering and error taxonomy).

    Args:
        state: Application state.

    Returns:
        ``ArtifactService`` instance.
    """
    return ArtifactService(
        repo=persistence_of(state.app_state).artifacts,
        storage=artifact_storage(state),
    )


async def save_metadata_with_rollback(
    service: ArtifactService,
    storage: ArtifactStorageBackend,
    artifact_id: str,
    updated: Artifact,
) -> None:
    """Save updated artifact metadata, rolling back storage on failure.

    Args:
        service: Artifact service wrapping the persistence repo.
        storage: Artifact content storage backend.
        artifact_id: Artifact identifier.
        updated: Updated artifact model.

    Raises:
        Exception: Any failure from ``service.save`` propagates after
            the storage-content rollback runs.  Narrowing this to
            ``PersistenceError`` only would leave stored content
            behind on any other failure mode (validation,
            serialisation, etc.).
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
    """
    try:
        await service.save(updated)
    except MemoryError, RecursionError:
        # Process-fatal builtins propagate before any rollback work
        # runs; project convention.
        raise
    except Exception as exc:
        # Catch-all rollback so any ``service.save`` failure undoes
        # the prior content write.  Without this, a non-
        # ``PersistenceError`` failure (validation, serialisation,
        # network, etc.) would leave the blob orphan in storage with
        # no metadata row to point at it.
        logger.warning(
            PERSISTENCE_ARTIFACT_SAVE_FAILED,
            artifact_id=artifact_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="metadata save failed, rolling back content",
        )
        try:
            await storage.delete(artifact_id)
        except MemoryError, RecursionError:
            # System-fatal builtins are ``Exception`` subclasses; re-raise
            # them before the catch-all so process-fatal conditions are
            # never logged-and-swallowed under the rollback path.
            raise
        except Exception as cleanup_exc:  # noqa: BLE001 -- rollback best-effort: log and continue
            logger.warning(
                PERSISTENCE_ARTIFACT_STORAGE_ROLLBACK_FAILED,
                artifact_id=artifact_id,
                error_type=type(cleanup_exc).__name__,
                error=safe_error_description(cleanup_exc),
            )
        raise
