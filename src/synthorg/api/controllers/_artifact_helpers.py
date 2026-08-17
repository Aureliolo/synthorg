"""Dependency construction and storage invariants for the artifact controller."""

from typing import Final

from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.core.artifact import Artifact
from synthorg.core.concurrency import RefcountedLockMap
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.persistence_errors import (
    ArtifactStorageFullError,
    ArtifactTooLargeError,
    RecordNotFoundError,
)
from synthorg.engine.artifacts.service import ArtifactService
from synthorg.engine.workspace.state import WorkspaceStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.artifact import (
    PERSISTENCE_ARTIFACT_SAVE_FAILED,
)
from synthorg.observability.events.persistence.artifact_storage import (
    PERSISTENCE_ARTIFACT_STORAGE_ROLLBACK_FAILED,
    PERSISTENCE_ARTIFACT_STORE_FAILED,
)
from synthorg.persistence.artifact_storage import ArtifactStorageBackend
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)

DEFAULT_CONTENT_TYPE: Final[str] = "application/octet-stream"

#: One lock per artifact, held for a whole upload. Module-level because the
#: service that would otherwise own it is rebuilt per request, so anything
#: narrower would hand each caller its own lock and serialise nothing.
_UPLOAD_LOCKS: Final[RefcountedLockMap[str]] = RefcountedLockMap()

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


async def _store_bytes(
    storage: ArtifactStorageBackend,
    artifact_id: str,
    content: bytes,
) -> int:
    """Write content, keeping the persistence detail out of the public message.

    Args:
        storage: Artifact content storage backend.
        artifact_id: Artifact identifier.
        content: Bytes to write.

    Returns:
        Number of bytes written.

    Raises:
        ArtifactTooLargeError: Re-raised with a generic message; the
            artifact id and byte sizes stay in the log and the exception
            chain rather than riding the 413 body.
        ArtifactStorageFullError: Re-raised with a generic message, for
            the same reason.
        Exception: Any other backend failure propagates with its type
            intact, after an operator-visible breadcrumb.
    """
    try:
        return await storage.store(artifact_id, content)
    except ArtifactTooLargeError as exc:
        _log_store_failure(artifact_id, exc, "artifact_too_large")
        msg = "Artifact content is too large"
        raise ArtifactTooLargeError(msg) from exc
    except ArtifactStorageFullError as exc:
        _log_store_failure(artifact_id, exc, "artifact_storage_full")
        msg = "Artifact storage is full"
        raise ArtifactStorageFullError(msg) from exc
    except Exception as exc:
        reraise_critical(exc)
        _log_store_failure(artifact_id, exc, "artifact_store_unexpected")
        raise


def _log_store_failure(artifact_id: str, exc: Exception, note: str) -> None:
    """Record a content-write failure under the store-failed cardinality.

    Args:
        artifact_id: Artifact identifier.
        exc: The failure being reported.
        note: Which of the store failure modes this was.
    """
    logger.warning(
        PERSISTENCE_ARTIFACT_STORE_FAILED,
        artifact_id=artifact_id,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
        note=note,
    )


async def store_content(
    service: ArtifactService,
    storage: ArtifactStorageBackend,
    artifact: Artifact,
    content: bytes,
) -> Artifact:
    """Write an artifact's content and record its size, as one step.

    Every upload for one artifact runs alone. The sequence is a
    read-modify-write across two stores (capture the bytes, overwrite them,
    record the new size) and interleaving two of them leaves the pair
    disagreeing in a way neither can detect: a rollback would restore the
    content a *different* upload had already superseded, against metadata
    describing that other upload.

    The lock is per artifact and per process, which is the whole
    serialisation boundary here because one backend process owns the
    storage tree; uploads for different artifacts never wait on each other.

    Args:
        service: Artifact service wrapping the persistence repo.
        storage: Artifact content storage backend.
        artifact: The artifact whose content is being written.
        content: Bytes to write.

    Returns:
        The artifact with ``size_bytes`` and ``content_type`` recorded.
    """
    async with _UPLOAD_LOCKS.acquire(artifact.id):
        previous = await replaced_content(storage, artifact.id)
        size = await _store_bytes(storage, artifact.id, content)
        updated = artifact.model_copy(
            update={
                "size_bytes": size,
                "content_type": artifact.content_type or DEFAULT_CONTENT_TYPE,
            },
        )
        await save_metadata_with_rollback(
            service, storage, artifact.id, updated, previous=previous
        )
        return updated
