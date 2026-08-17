"""Dependency construction and storage invariants for the artifact controller."""

from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.core.artifact import Artifact
from synthorg.core.persistence_errors import RecordNotFoundError
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
    # ``State.app_state`` is untyped by Litestar, so the slice field arrives as
    # ``Any``; naming the declared type here is what lets the guard's return
    # type bind to it instead of propagating ``Any`` to every caller.
    storage: ArtifactStorageBackend | None = state.app_state.slice(
        WorkspaceStateSlice
    ).artifact_storage
    return require_service(storage, "Artifact Storage")


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


async def replaced_content(
    storage: ArtifactStorageBackend,
    artifact_id: str,
) -> bytes | None:
    """Read the content an upload is about to overwrite.

    ``store`` overwrites in place, so once it runs the previous bytes are
    unrecoverable and a rollback that deletes would destroy the content the
    upload REPLACED rather than the content it wrote. Reading first is what
    makes the undo an undo. Only a replacement pays for it: an artifact with
    no content yet answers ``False`` and returns without a read.

    Args:
        storage: Artifact content storage backend.
        artifact_id: Artifact identifier.

    Returns:
        The current content, or ``None`` when the artifact has none.
    """
    if not await storage.exists(artifact_id):
        return None
    try:
        return await storage.retrieve(artifact_id)
    except RecordNotFoundError:
        # Deleted between the probe and the read; there is nothing to
        # restore, which is the same state as never having had content.
        return None


async def _restore_content(
    storage: ArtifactStorageBackend,
    artifact_id: str,
    previous: bytes | None,
) -> None:
    """Put storage back as the upload found it.

    Args:
        storage: Artifact content storage backend.
        artifact_id: Artifact identifier.
        previous: Content the upload overwrote, or ``None`` when the
            upload created it.

    Raises:
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
    """
    try:
        if previous is None:
            await storage.delete(artifact_id)
        else:
            await storage.store(artifact_id, previous)
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
            restored=previous is not None,
        )


async def save_metadata_with_rollback(
    service: ArtifactService,
    storage: ArtifactStorageBackend,
    artifact_id: str,
    updated: Artifact,
    *,
    previous: bytes | None,
) -> None:
    """Save updated artifact metadata, rolling back storage on failure.

    Args:
        service: Artifact service wrapping the persistence repo.
        storage: Artifact content storage backend.
        artifact_id: Artifact identifier.
        updated: Updated artifact model.
        previous: Content the upload overwrote (from
            :func:`replaced_content`), or ``None`` when the upload
            created it.

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
        await _restore_content(storage, artifact_id, previous)
        raise
