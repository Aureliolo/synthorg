"""Artifact controller -- endpoints for artifact management, storage, and retrieval."""

from typing import Annotated, Any

from litestar import Controller, Request, Response, delete, get, post, put
from litestar.datastructures import State  # noqa: TC002
from litestar.enums import RequestEncodingType
from litestar.params import Body, Parameter

from synthorg.api.channels import CHANNEL_ARTIFACTS, publish_ws_event
from synthorg.api.dto import ApiResponse, CreateArtifactRequest, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import CursorLimit, CursorParam, paginate_cursor
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.ws_models import WsEventType
from synthorg.core.artifact import Artifact
from synthorg.core.domain_errors import (
    ArtifactRejectedTooLargeError,
    ArtifactStorageRejectedFullError,
    NotFoundError,
    ValidationError,
)
from synthorg.core.enums import ArtifactType
from synthorg.core.persistence_errors import (
    ArtifactStorageFullError,
    ArtifactTooLargeError,
    PersistenceError,
    RecordNotFoundError,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.artifacts.service import ArtifactService
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_ARTIFACT_CONTENT_MISSING,
    PERSISTENCE_ARTIFACT_FETCH_FAILED,
    PERSISTENCE_ARTIFACT_SAVE_FAILED,
    PERSISTENCE_ARTIFACT_STORAGE_DELETE_FAILED,
    PERSISTENCE_ARTIFACT_STORAGE_ROLLBACK_FAILED,
    PERSISTENCE_ARTIFACT_STORE_FAILED,
    PERSISTENCE_ARTIFACT_STORED,
)

logger = get_logger(__name__)


def _service(state: State) -> ArtifactService:
    """Build the per-request :class:`ArtifactService` instance."""
    return ArtifactService(repo=state.app_state.persistence.artifacts)


_SAFE_CONTENT_TYPES = frozenset(
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

TaskIdFilter = Annotated[
    NotBlankStr | None,
    Parameter(
        required=False,
        max_length=QUERY_MAX_LENGTH,
        description="Filter by originating task ID",
    ),
]

CreatedByFilter = Annotated[
    NotBlankStr | None,
    Parameter(
        required=False,
        max_length=QUERY_MAX_LENGTH,
        description="Filter by creator agent ID",
    ),
]

TypeFilter = Annotated[
    NotBlankStr | None,
    Parameter(
        required=False,
        query="type",
        max_length=QUERY_MAX_LENGTH,
        description="Filter by artifact type",
    ),
]


async def _save_metadata_with_rollback(
    service: ArtifactService,
    storage: Any,
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
        PersistenceError: If the metadata save fails (after rollback attempt).
    """
    try:
        await service.save(updated)
    except PersistenceError as exc:
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
        except Exception as cleanup_exc:
            logger.warning(
                PERSISTENCE_ARTIFACT_STORAGE_ROLLBACK_FAILED,
                artifact_id=artifact_id,
                error_type=type(cleanup_exc).__name__,
                error=safe_error_description(cleanup_exc),
            )
        raise


class ArtifactController(Controller):
    """Controller for artifact listing, creation, deletion, and content storage."""

    path = "/artifacts"
    tags = ("artifacts",)

    @get(guards=[require_read_access])
    async def list_artifacts(  # noqa: PLR0913
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = 50,
        task_id: TaskIdFilter = None,
        created_by: CreatedByFilter = None,
        type: TypeFilter = None,  # noqa: A002
    ) -> PaginatedResponse[Artifact]:
        """List artifacts with optional filters.

        Args:
            state: Application state.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.
            task_id: Filter by originating task ID.
            created_by: Filter by creator agent ID.
            type: Filter by artifact type.

        Returns:
            Paginated list of artifacts.

        Raises:
            ValidationError: If ``type`` is not a known ``ArtifactType``
                value (mapped centrally to HTTP 422 by
                ``EXCEPTION_HANDLERS``).
        """
        parsed_type: ArtifactType | None = None
        if type is not None:
            try:
                parsed_type = ArtifactType(type)
            except ValueError as exc:
                valid = ", ".join(e.value for e in ArtifactType)
                msg = f"Invalid artifact type: {type!r}. Valid values: {valid}"
                logger.warning(
                    PERSISTENCE_ARTIFACT_FETCH_FAILED,
                    rejected_type=type,
                    reason="invalid_artifact_type",
                )
                raise ValidationError(msg) from exc

        artifacts = await _service(state).list_artifacts(
            task_id=task_id,
            created_by=created_by,
            artifact_type=parsed_type,
        )
        page, meta = paginate_cursor(
            artifacts,
            limit=limit,
            cursor=cursor,
            secret=state.app_state.cursor_secret,
        )
        return PaginatedResponse[Artifact](data=page, pagination=meta)

    @get("/{artifact_id:str}", guards=[require_read_access])
    async def get_artifact(
        self,
        state: State,
        artifact_id: PathId,
    ) -> ApiResponse[Artifact]:
        """Get an artifact by ID.

        Args:
            state: Application state.
            artifact_id: Artifact identifier.

        Returns:
            The artifact metadata.

        Raises:
            NotFoundError: If the artifact does not exist (HTTP 404).
        """
        artifact = await _service(state).get(artifact_id)
        if artifact is None:
            msg = f"Artifact {artifact_id!r} not found"
            logger.warning(
                PERSISTENCE_ARTIFACT_FETCH_FAILED,
                artifact_id=artifact_id,
                operation="read",
            )
            raise NotFoundError(msg)
        return ApiResponse[Artifact](data=artifact)

    @post(guards=[require_write_access], status_code=201)
    async def create_artifact(
        self,
        request: Request[Any, Any, Any],
        state: State,
        data: CreateArtifactRequest,
    ) -> ApiResponse[Artifact]:
        """Create a new artifact.

        Args:
            request: The incoming request.
            state: Application state.
            data: Artifact creation payload.

        Returns:
            The created artifact with generated ID.
        """
        artifact = await _service(state).create(
            artifact_type=data.type,
            path=data.path,
            task_id=data.task_id,
            created_by=data.created_by,
            description=data.description,
            content_type=data.content_type,
            project_id=data.project_id,
        )
        publish_ws_event(
            request,
            WsEventType.ARTIFACT_CREATED,
            CHANNEL_ARTIFACTS,
            {
                "artifact_id": artifact.id,
                "task_id": artifact.task_id,
                "created_by": artifact.created_by,
                "type": artifact.type.value,
            },
        )
        return ApiResponse[Artifact](data=artifact)

    @delete(
        "/{artifact_id:str}",
        guards=[require_write_access],
        status_code=200,
    )
    async def delete_artifact(
        self,
        request: Request[Any, Any, Any],
        state: State,
        artifact_id: PathId,
    ) -> ApiResponse[None]:
        """Delete an artifact and its stored content.

        Args:
            request: The incoming request.
            state: Application state.
            artifact_id: Artifact identifier.

        Returns:
            ``ApiResponse`` with ``data=None`` on success.

        Raises:
            NotFoundError: If the artifact does not exist (HTTP 404).
        """
        service = _service(state)
        artifact = await service.get(artifact_id)
        if artifact is None:
            msg = f"Artifact {artifact_id!r} not found"
            logger.warning(
                PERSISTENCE_ARTIFACT_FETCH_FAILED,
                artifact_id=artifact_id,
                operation="delete",
            )
            raise NotFoundError(msg)
        # Delete storage content first -- if this fails, metadata
        # MUST still exist so the inconsistency is detectable (vs.
        # the reverse order where metadata is gone but the orphaned
        # blob is invisible).  Do NOT continue to ``service.delete``
        # on storage failure: that would emit ``ARTIFACT_DELETED`` and
        # remove the metadata row even though the blob is still
        # present, causing data-state drift the metadata-first invariant
        # exists to prevent.
        storage = state.app_state.artifact_storage
        try:
            await storage.delete(artifact_id)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                PERSISTENCE_ARTIFACT_STORAGE_DELETE_FAILED,
                artifact_id=artifact_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        await service.delete(artifact_id)
        publish_ws_event(
            request,
            WsEventType.ARTIFACT_DELETED,
            CHANNEL_ARTIFACTS,
            {"artifact_id": artifact_id, "task_id": artifact.task_id},
        )
        return ApiResponse[None](data=None)

    @put(
        "/{artifact_id:str}/content",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("artifacts.upload", key="user"),
        ],
        media_type="application/json",
    )
    async def upload_content(
        self,
        request: Request[Any, Any, Any],
        state: State,
        artifact_id: PathId,
        data: Annotated[
            bytes,
            Body(media_type=RequestEncodingType.MULTI_PART),
        ],
    ) -> ApiResponse[Artifact]:
        """Upload binary content for an artifact.

        Validates size limits before storing.

        Args:
            request: The incoming request.
            state: Application state.
            artifact_id: Artifact identifier.
            data: Binary content.

        Returns:
            Updated artifact metadata with size_bytes set.
        """
        service = _service(state)
        artifact = await service.get(artifact_id)
        if artifact is None:
            msg = f"Artifact {artifact_id!r} not found"
            logger.warning(
                PERSISTENCE_ARTIFACT_FETCH_FAILED,
                artifact_id=artifact_id,
                error_type="artifact_not_found",
                note="upload_content_target_missing",
            )
            raise NotFoundError(msg)

        storage = state.app_state.artifact_storage
        try:
            size = await storage.store(artifact_id, data)
        except ArtifactTooLargeError as exc:
            logger.warning(
                PERSISTENCE_ARTIFACT_STORE_FAILED,
                artifact_id=artifact_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="artifact_too_large",
            )
            raise ArtifactRejectedTooLargeError from exc
        except ArtifactStorageFullError as exc:
            logger.warning(
                PERSISTENCE_ARTIFACT_STORE_FAILED,
                artifact_id=artifact_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="artifact_storage_full",
            )
            raise ArtifactStorageRejectedFullError from exc
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            # Catch-all so any other backend / storage failure leaves an
            # operator-visible breadcrumb on the standardized error path
            # this PR is widening; the original exception still
            # propagates with type intact.
            logger.warning(
                PERSISTENCE_ARTIFACT_STORE_FAILED,
                artifact_id=artifact_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="artifact_store_unexpected",
            )
            raise

        updated = artifact.model_copy(
            update={
                "size_bytes": size,
                "content_type": (artifact.content_type or "application/octet-stream"),
            },
        )
        await _save_metadata_with_rollback(service, storage, artifact_id, updated)
        logger.info(
            PERSISTENCE_ARTIFACT_STORED,
            artifact_id=artifact_id,
            size_bytes=size,
        )
        publish_ws_event(
            request,
            WsEventType.ARTIFACT_CONTENT_UPLOADED,
            CHANNEL_ARTIFACTS,
            {
                "artifact_id": artifact_id,
                "size_bytes": size,
                "content_type": updated.content_type,
            },
        )
        return ApiResponse[Artifact](data=updated)

    @get(
        "/{artifact_id:str}/content",
        guards=[require_read_access],
        media_type="application/octet-stream",
    )
    async def download_content(
        self,
        state: State,
        artifact_id: PathId,
    ) -> Response:  # type: ignore[type-arg]
        """Download binary content for an artifact.

        Both 404 branches raise ``NotFoundError`` *before* any binary
        bytes are streamed, so the central exception handler can swap
        the response shape from ``application/octet-stream`` to the
        RFC 9457 JSON envelope without colliding with already-sent
        headers.

        Args:
            state: Application state.
            artifact_id: Artifact identifier.

        Returns:
            Binary content with appropriate content type.

        Raises:
            NotFoundError: If the artifact metadata or content is
                missing (HTTP 404).
        """
        artifact = await _service(state).get(artifact_id)
        if artifact is None:
            msg = f"Artifact {artifact_id!r} not found"
            logger.warning(
                PERSISTENCE_ARTIFACT_FETCH_FAILED,
                artifact_id=artifact_id,
                operation="download",
            )
            raise NotFoundError(msg)

        storage = state.app_state.artifact_storage
        try:
            content = await storage.retrieve(artifact_id)
        except RecordNotFoundError as exc:
            logger.warning(
                PERSISTENCE_ARTIFACT_CONTENT_MISSING,
                artifact_id=artifact_id,
            )
            msg = f"Artifact content for {artifact_id!r} not found"
            raise NotFoundError(msg) from exc
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            # Catch-all so any backend / storage failure on the
            # download path leaves an operator-visible breadcrumb
            # alongside the standardized error path; the original
            # exception still propagates with type intact.
            logger.error(  # noqa: TRY400
                PERSISTENCE_ARTIFACT_CONTENT_MISSING,
                artifact_id=artifact_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="artifact_retrieve_unexpected",
            )
            raise

        raw_ct = artifact.content_type or "application/octet-stream"
        fallback = "application/octet-stream"
        safe_ct = raw_ct if raw_ct in _SAFE_CONTENT_TYPES else fallback
        return Response(
            content=content,
            status_code=200,
            media_type=safe_ct,
            headers={"Content-Disposition": "attachment"},
        )
