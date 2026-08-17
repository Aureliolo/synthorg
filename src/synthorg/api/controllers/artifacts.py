"""Artifact controller -- endpoints for artifact management, storage, and retrieval."""

from typing import Annotated, Final

from litestar import Controller, Request, Response, delete, get, post, put
from litestar.datastructures import State
from litestar.enums import RequestEncodingType
from litestar.params import Body, QueryParameter

from synthorg.api._read_names import agent_name_map
from synthorg.api.channels import CHANNEL_ARTIFACTS, publish_ws_event
from synthorg.api.controllers._artifact_helpers import (
    SAFE_CONTENT_TYPES,
    artifact_service,
    artifact_storage,
    save_metadata_with_rollback,
)
from synthorg.api.dto import ApiResponse, CreateArtifactRequest, PaginatedResponse
from synthorg.api.dto_named_rows import ArtifactRow
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.responses import require_resource_or_404
from synthorg.api.ws_models import WsEventType
from synthorg.core.artifact import ArtifactType
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import (
    NotFoundError,
    ValidationError,
)
from synthorg.core.persistence_errors import (
    ArtifactStorageFullError,
    ArtifactTooLargeError,
    RecordNotFoundError,
)
from synthorg.core.types import NotBlankStr
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import API_VALIDATION_FAILED
from synthorg.observability.events.persistence.artifact import (
    PERSISTENCE_ARTIFACT_METADATA_MISSING,
)
from synthorg.observability.events.persistence.artifact_storage import (
    PERSISTENCE_ARTIFACT_RETRIEVE_FAILED,
    PERSISTENCE_ARTIFACT_STORE_FAILED,
    PERSISTENCE_ARTIFACT_STORED,
)

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50

TaskIdFilter = Annotated[
    NotBlankStr | None,
    QueryParameter(
        required=False,
        max_length=QUERY_MAX_LENGTH,
        description="Filter by originating task ID",
    ),
]

CreatedByFilter = Annotated[
    NotBlankStr | None,
    QueryParameter(
        required=False,
        max_length=QUERY_MAX_LENGTH,
        description="Filter by creator agent ID",
    ),
]

TypeFilter = Annotated[
    NotBlankStr | None,
    QueryParameter(
        required=False,
        name="type",
        max_length=QUERY_MAX_LENGTH,
        description="Filter by artifact type",
    ),
]


class ArtifactController(Controller):
    """Controller for artifact listing, creation, deletion, and content storage."""

    path = "/artifacts"
    tags = ("artifacts",)

    @get(guards=[require_read_access])
    async def list_artifacts(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
        task_id: TaskIdFilter = None,
        created_by: CreatedByFilter = None,
        type: TypeFilter = None,  # noqa: A002
    ) -> PaginatedResponse[ArtifactRow]:
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
                # Validation rejection at the request boundary; route
                # through ``API_VALIDATION_FAILED`` so query-shape
                # rejections don't collapse into the
                # ``PERSISTENCE_ARTIFACT_FETCH_FAILED`` bucket used for
                # actual fetch failures.
                logger.warning(
                    API_VALIDATION_FAILED,
                    field="type",
                    rejected_value=type,
                    reason=msg,
                )
                raise ValidationError(msg) from exc

        artifacts = await artifact_service(state).list_artifacts(
            task_id=task_id,
            created_by=created_by,
            artifact_type=parsed_type,
        )
        page, meta = paginate_cursor(
            artifacts,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(state.app_state),
        )
        names = await agent_name_map(state.app_state)
        return PaginatedResponse[ArtifactRow](
            data=tuple(ArtifactRow.of(item, names) for item in page),
            pagination=meta,
        )

    @get("/{artifact_id:str}", guards=[require_read_access])
    async def get_artifact(
        self,
        state: State,
        artifact_id: PathId,
    ) -> ApiResponse[ArtifactRow]:
        """Get an artifact by ID.

        Args:
            state: Application state.
            artifact_id: Artifact identifier.

        Returns:
            The artifact metadata.

        Raises:
            NotFoundError: If the artifact does not exist (HTTP 404).
        """
        artifact = require_resource_or_404(
            await artifact_service(state).get(artifact_id),
            resource_type="Artifact",
            identifier=artifact_id,
            log_event=PERSISTENCE_ARTIFACT_METADATA_MISSING,
            operation="read",
            extra_log_kwargs={"artifact_id": artifact_id},
        )
        return ApiResponse[ArtifactRow](
            data=ArtifactRow.of(artifact, await agent_name_map(state.app_state))
        )

    @post(
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("artifacts.create", key="user"),
        ],
        status_code=201,
    )
    async def create_artifact(
        self,
        request: Request[object, object, State],
        state: State,
        data: CreateArtifactRequest,
    ) -> ApiResponse[ArtifactRow]:
        """Create a new artifact.

        Args:
            request: The incoming request.
            state: Application state.
            data: Artifact creation payload.

        Returns:
            The created artifact with generated ID.
        """
        artifact = await artifact_service(state).create(
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
        return ApiResponse[ArtifactRow](
            data=ArtifactRow.of(artifact, await agent_name_map(state.app_state))
        )

    @delete(
        "/{artifact_id:str}",
        guards=[require_write_access],
        status_code=200,
    )
    async def delete_artifact(
        self,
        request: Request[object, object, State],
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
        service = artifact_service(state)
        artifact = require_resource_or_404(
            await service.get(artifact_id),
            resource_type="Artifact",
            identifier=artifact_id,
            log_event=PERSISTENCE_ARTIFACT_METADATA_MISSING,
            operation="delete",
            extra_log_kwargs={"artifact_id": artifact_id},
        )
        # Storage-first delete + persistence delete with the right
        # error taxonomy is owned by the service so the controller
        # stays out of the mixed-orchestration role; see
        # ``ArtifactService.delete_with_content`` for the full
        # contract (storage failure preserves the metadata row so the
        # inconsistency is detectable, etc.).
        deleted = await service.delete_with_content(artifact_id)
        if not deleted:
            # TOCTOU: the row was present at ``service.get`` above but
            # vanished before ``delete_with_content`` ran (concurrent
            # delete / cleanup job).  Do NOT publish the WS event --
            # claiming a deletion that didn't happen here would mislead
            # subscribers.  Surface as 404 so clients see the same
            # outcome as if the row was missing on entry.
            msg = f"Artifact {artifact_id!r} not found"
            logger.warning(
                PERSISTENCE_ARTIFACT_METADATA_MISSING,
                artifact_id=artifact_id,
                operation="delete",
                note="concurrent_delete",
            )
            raise NotFoundError(msg)
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
        request: Request[object, object, State],
        state: State,
        artifact_id: PathId,
        data: Annotated[
            bytes,
            Body(media_type=RequestEncodingType.MULTI_PART),
        ],
    ) -> ApiResponse[ArtifactRow]:
        """Upload binary content for an artifact.

        Validates size limits before storing.

        Args:
            request: The incoming request.
            state: Application state.
            artifact_id: Artifact identifier.
            data: Binary content.

        Returns:
            Updated artifact metadata with size_bytes set.

        Raises:
            ArtifactTooLargeError: Raised on the corresponding failure path.
            ArtifactStorageFullError: Raised on the corresponding failure path.
            Exception: Raised on the corresponding failure path.
        """
        service = artifact_service(state)
        artifact = require_resource_or_404(
            await service.get(artifact_id),
            resource_type="Artifact",
            identifier=artifact_id,
            log_event=PERSISTENCE_ARTIFACT_METADATA_MISSING,
            operation="upload",
            extra_log_kwargs={
                "artifact_id": artifact_id,
                "note": "upload_content_target_missing",
            },
        )

        storage = artifact_storage(state)
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
            # Re-raise with the generic public message: the persistence
            # detail (artifact id + byte sizes) stays in the log and the
            # exception chain and must not reach the client on the 413 body.
            msg = "Artifact content is too large"
            raise ArtifactTooLargeError(msg) from exc
        except ArtifactStorageFullError as exc:
            logger.warning(
                PERSISTENCE_ARTIFACT_STORE_FAILED,
                artifact_id=artifact_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="artifact_storage_full",
            )
            msg = "Artifact storage is full"
            raise ArtifactStorageFullError(msg) from exc
        except Exception as exc:
            reraise_critical(exc)
            # Catch-all so any other backend / storage failure leaves an
            # operator-visible breadcrumb on the standardized error path;
            # the original exception still propagates with type intact.
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
        await save_metadata_with_rollback(service, storage, artifact_id, updated)
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
        return ApiResponse[ArtifactRow](
            data=ArtifactRow.of(updated, await agent_name_map(state.app_state))
        )

    @get(
        "/{artifact_id:str}/content",
        guards=[
            require_read_access,
            per_op_rate_limit_from_policy("artifacts.download", key="user"),
        ],
        media_type="application/octet-stream",
    )
    async def download_content(
        self,
        state: State,
        artifact_id: PathId,
    ) -> Response:  # type: ignore[type-arg]
        """Download binary content for an artifact.

        Both 404 branches raise before any binary bytes are streamed, so
        the central exception handler can swap the response shape from
        ``application/octet-stream`` to the RFC 9457 JSON envelope without
        colliding with already-sent headers.

        Args:
            state: Application state.
            artifact_id: Artifact identifier.

        Returns:
            Binary content with appropriate content type.

        Raises:
            NotFoundError: If the artifact metadata is missing (HTTP 404,
                ``RESOURCE_NOT_FOUND``).
            RecordNotFoundError: If the metadata exists but the content
                blob is absent from storage (HTTP 404, ``RECORD_NOT_FOUND``);
                propagated so clients can distinguish the two not-found
                conditions by code.
        """
        artifact = require_resource_or_404(
            await artifact_service(state).get(artifact_id),
            resource_type="Artifact",
            identifier=artifact_id,
            log_event=PERSISTENCE_ARTIFACT_METADATA_MISSING,
            operation="download",
            extra_log_kwargs={"artifact_id": artifact_id},
        )

        storage = artifact_storage(state)
        try:
            content = await storage.retrieve(artifact_id)
        except RecordNotFoundError:
            # Missing content blob is an ordinary 404: it carries its own
            # ``RECORD_NOT_FOUND`` wire contract and propagates to the
            # persistence handler (which scrubs the message to a safe generic
            # string). Caught BEFORE the storage-fault breadcrumb below so a
            # routine not-found does not log at ERROR under the retrieve-failed
            # event.
            raise
        # Any other failure is a genuine storage fault; leave an
        # operator-visible breadcrumb under the retrieve-failed event.
        except Exception as exc:
            reraise_critical(exc)
            # Catch-all so any backend / storage failure on the
            # download path leaves an operator-visible breadcrumb
            # alongside the standardized error path; the original
            # exception still propagates with type intact.  Route
            # through ``PERSISTENCE_ARTIFACT_RETRIEVE_FAILED`` (the
            # storage-retrieve cardinality) so blob-store outages are
            # visible alongside metadata-fetch failures without sharing
            # a counter.
            log_exception_redacted(
                logger,
                PERSISTENCE_ARTIFACT_RETRIEVE_FAILED,
                exc,
                artifact_id=artifact_id,
                operation="download",
                note="artifact_retrieve_unexpected",
            )
            raise

        raw_ct = artifact.content_type or "application/octet-stream"
        fallback = "application/octet-stream"
        safe_ct = raw_ct if raw_ct in SAFE_CONTENT_TYPES else fallback
        return Response(
            content=content,
            status_code=200,
            media_type=safe_ct,
            headers={"Content-Disposition": "attachment"},
        )
