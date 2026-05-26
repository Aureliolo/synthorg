"""Ontology REST API controller.

Provides entity CRUD, versioning, drift detection, and admin
endpoints for the ontology subsystem.
"""

from datetime import UTC, datetime
from typing import Annotated, Final

from litestar import Controller, delete, get, post, put
from litestar.datastructures import State  # noqa: TC002
from litestar.params import PathParameter, QueryParameter
from litestar.status_codes import HTTP_204_NO_CONTENT

from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.dto_ontology import (
    CreateEntityRequest,
    DriftReportResponse,
    EntityFieldResponse,
    EntityRelationResponse,
    EntityResponse,
    EntityVersionResponse,
    UpdateEntityRequest,
)
from synthorg.api.guards import (
    require_read_access,
    require_write_access,
)
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    encode_countless_seek_meta,
    paginate_cursor,
)
from synthorg.api.path_params import PathName  # noqa: TC001
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.domain_errors import NotFoundError, ValidationError
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_REQUEST_ERROR,
    API_RESOURCE_NOT_FOUND,
)
from synthorg.observability.events.ontology import (
    ONTOLOGY_ADMIN_SYNC_COMPLETED,
    ONTOLOGY_DRIFT_CHECK_COMPLETED,
    ONTOLOGY_DRIFT_CHECK_STARTED,
)
from synthorg.ontology.errors import (
    OntologyDuplicateError,
    OntologyNotFoundError,
)
from synthorg.ontology.models import (
    DriftReport,
    EntityDefinition,
    EntityField,
    EntityRelation,
    EntitySource,
    EntityTier,
)

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


def _entity_to_response(entity: EntityDefinition) -> EntityResponse:
    """Convert an EntityDefinition to an EntityResponse.

    Returns:
        ``EntityResponse`` instance.
    """
    return EntityResponse(
        name=entity.name,
        tier=entity.tier,
        source=entity.source,
        definition=entity.definition,
        fields=tuple(
            EntityFieldResponse(
                name=f.name,
                type_hint=f.type_hint,
                description=f.description,
            )
            for f in entity.fields
        ),
        constraints=entity.constraints,
        disambiguation=entity.disambiguation,
        relationships=tuple(
            EntityRelationResponse(
                target=r.target,
                relation=r.relation,
                description=r.description,
            )
            for r in entity.relationships
        ),
        created_by=entity.created_by,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


def _drift_report_to_response(
    report: DriftReport,
) -> DriftReportResponse:
    """Convert a DriftReport to a DriftReportResponse.

    Returns:
        ``DriftReportResponse`` instance.
    """
    from synthorg.api.dto_ontology import DriftAgentResponse  # noqa: PLC0415

    return DriftReportResponse(
        entity_name=report.entity_name,
        divergence_score=report.divergence_score,
        divergent_agents=tuple(
            DriftAgentResponse(
                agent_id=a.agent_id,
                divergence_score=a.divergence_score,
                details=a.details,
            )
            for a in report.divergent_agents
        ),
        canonical_version=report.canonical_version,
        recommendation=report.recommendation,
    )


class OntologyController(Controller):
    """Entity definition CRUD, versioning, and drift detection."""

    path = "/ontology"
    tags = ("ontology",)
    guards = [require_read_access]  # noqa: RUF012

    # ── Entity CRUD ────────────────────────────────────────────

    @get("/entities")
    async def list_entities(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
        tier: Annotated[
            str | None,
            QueryParameter(description="Filter to entity definitions in this tier."),
        ] = None,
    ) -> PaginatedResponse[EntityResponse]:
        """List all entity definitions, filterable by tier.

        Returns:
            ``PaginatedResponse[EntityResponse]`` instance.

        Raises:
            ValidationError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        svc = app_state.ontology_service

        tier_filter: EntityTier | None = None
        if tier is not None:
            try:
                tier_filter = EntityTier(tier)
            except ValueError:
                allowed = ", ".join(m.value for m in EntityTier)
                msg = f"Invalid tier {tier!r}. Allowed: {allowed}"
                logger.warning(
                    API_REQUEST_ERROR,
                    reason="invalid_tier",
                    tier=tier,
                )
                raise ValidationError(msg)  # noqa: B904
        entities = await svc.list_entities(tier=tier_filter)

        responses = tuple(_entity_to_response(e) for e in entities)
        page, meta = paginate_cursor(
            responses,
            limit=limit,
            cursor=cursor,
            secret=app_state.cursor_secret,
        )
        return PaginatedResponse(data=page, pagination=meta)

    @get("/entities/{name:str}")
    async def get_entity(
        self,
        state: State,
        name: PathName,
    ) -> ApiResponse[EntityResponse]:
        """Get a single entity definition by name.

        Returns:
            ``ApiResponse[EntityResponse]`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        try:
            entity = await app_state.ontology_service.get(name)
        except OntologyNotFoundError:
            msg = "Entity not found"
            logger.info(
                API_RESOURCE_NOT_FOUND,
                resource="entity",
                name=name,
            )
            raise NotFoundError(msg)  # noqa: B904
        return ApiResponse(data=_entity_to_response(entity))

    @post(
        "/entities",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("ontology.create_entity", key="user"),
        ],
        status_code=201,
    )
    async def create_entity(
        self,
        state: State,
        data: CreateEntityRequest,
    ) -> ApiResponse[EntityResponse]:
        """Create a new USER-tier entity definition.

        Returns:
            ``ApiResponse[EntityResponse]`` instance.

        Raises:
            ValidationError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        now = datetime.now(UTC)

        entity = EntityDefinition(
            name=data.name,
            tier=EntityTier.USER,
            source=EntitySource.API,
            definition=data.definition,
            fields=tuple(
                EntityField(
                    name=f.name,
                    type_hint=f.type_hint,
                    description=f.description,
                )
                for f in data.fields
            ),
            constraints=data.constraints,
            disambiguation=data.disambiguation,
            relationships=tuple(
                EntityRelation(
                    target=r.target,
                    relation=r.relation,
                    description=r.description,
                )
                for r in data.relationships
            ),
            created_by="api",
            created_at=now,
            updated_at=now,
        )

        try:
            await app_state.ontology_service.register(entity)
        except OntologyDuplicateError:
            msg = "Entity already exists"
            logger.warning(
                API_REQUEST_ERROR,
                reason="duplicate_entity",
                name=data.name,
            )
            raise ValidationError(msg)  # noqa: B904

        return ApiResponse(data=_entity_to_response(entity))

    @put(
        "/entities/{name:str}",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("ontology.update_entity", key="user"),
        ],
    )
    async def update_entity(
        self,
        state: State,
        name: PathName,
        data: UpdateEntityRequest,
    ) -> ApiResponse[EntityResponse]:
        """Update an entity definition.

        Returns:
            ``ApiResponse[EntityResponse]`` instance.

        Raises:
            ValidationError: Raised on the corresponding failure path.
            NotFoundError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        svc = app_state.ontology_service

        try:
            existing = await svc.get(name)
        except OntologyNotFoundError:
            msg = "Entity not found"
            logger.info(API_RESOURCE_NOT_FOUND, resource="entity", name=name)
            raise NotFoundError(msg)  # noqa: B904

        if existing.tier == EntityTier.CORE and any(
            (
                data.definition is not None,
                data.fields is not None,
                data.constraints is not None,
                data.disambiguation is not None,
                data.relationships is not None,
            ),
        ):
            msg = "CORE entities cannot be modified via API"
            logger.warning(
                API_REQUEST_ERROR,
                reason="core_entity_modification",
                name=name,
            )
            raise ValidationError(msg)

        updates: dict[str, object] = {}
        if data.definition is not None:
            updates["definition"] = data.definition
        if data.disambiguation is not None:
            updates["disambiguation"] = data.disambiguation
        if data.constraints is not None:
            updates["constraints"] = data.constraints
        if existing.tier != EntityTier.CORE:
            if data.fields is not None:
                updates["fields"] = tuple(
                    EntityField(
                        name=f.name,
                        type_hint=f.type_hint,
                        description=f.description,
                    )
                    for f in data.fields
                )
            if data.relationships is not None:
                updates["relationships"] = tuple(
                    EntityRelation(
                        target=r.target,
                        relation=r.relation,
                        description=r.description,
                    )
                    for r in data.relationships
                )

        if not updates:
            return ApiResponse(data=_entity_to_response(existing))
        updates["updated_at"] = datetime.now(UTC)
        updated = existing.model_copy(update=updates)
        await svc.update(updated)
        return ApiResponse(data=_entity_to_response(updated))

    @delete(
        "/entities/{name:str}",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("ontology.delete_entity", key="user"),
        ],
        status_code=HTTP_204_NO_CONTENT,
    )
    async def delete_entity(
        self,
        state: State,
        name: PathName,
    ) -> None:
        """Delete a USER-tier entity definition.

        Raises:
            ValidationError: Raised on the corresponding failure path.
            NotFoundError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        svc = app_state.ontology_service

        try:
            entity = await svc.get(name)
        except OntologyNotFoundError:
            msg = "Entity not found"
            logger.info(API_RESOURCE_NOT_FOUND, resource="entity", name=name)
            raise NotFoundError(msg)  # noqa: B904

        if entity.tier == EntityTier.CORE:
            msg = "CORE entities cannot be deleted via API"
            logger.warning(
                API_REQUEST_ERROR,
                reason="core_entity_delete_rejected",
                name=name,
                tier=entity.tier.value,
            )
            raise ValidationError(msg)

        await svc.delete(name)

    # ── Versioning ─────────────────────────────────────────────

    @get("/entities/{name:str}/versions")
    async def list_entity_versions(
        self,
        state: State,
        name: PathName,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[EntityVersionResponse]:
        """List all versions of an entity definition.

        Returns:
            ``PaginatedResponse[EntityVersionResponse]`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        svc = app_state.ontology_service

        try:
            await svc.get(name)
        except OntologyNotFoundError:
            msg = "Entity not found"
            raise NotFoundError(msg)  # noqa: B904

        # Decode the cursor at the controller so the repo can honour a
        # true ``LIMIT / OFFSET`` instead of streaming every version.
        # Request ``limit + 1`` so ``paginate_cursor`` can detect that
        # another page follows without issuing a second COUNT query;
        # that keeps the handler O(limit) rather than O(n).
        offset = (
            0
            if cursor is None
            else decode_cursor(cursor, secret=app_state.cursor_secret)
        )
        versions = await svc.list_versions(
            name,
            limit=limit + 1,
            offset=offset,
        )
        meta = encode_countless_seek_meta(
            offset=offset,
            fetched_rows=len(versions),
            limit=limit,
            secret=app_state.cursor_secret,
        )
        window = versions[:limit]
        responses = tuple(
            EntityVersionResponse(
                entity_id=v.entity_id,
                version=v.version,
                content_hash=v.content_hash,
                snapshot=_entity_to_response(v.snapshot),
                saved_by=v.saved_by,
                saved_at=v.saved_at,
            )
            for v in window
        )
        return PaginatedResponse(data=responses, pagination=meta)

    @get("/entities/{name:str}/versions/{version:int}")
    async def get_entity_version(
        self,
        state: State,
        name: PathName,
        version: Annotated[
            int,
            PathParameter(description="Entity definition version to fetch."),
        ],
    ) -> ApiResponse[EntityVersionResponse]:
        """Get a specific version snapshot.

        Returns:
            ``ApiResponse[EntityVersionResponse]`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        svc = app_state.ontology_service

        v = await svc.get_version(name, version)
        if v is None:
            msg = "Version not found"
            raise NotFoundError(msg)

        return ApiResponse(
            data=EntityVersionResponse(
                entity_id=v.entity_id,
                version=v.version,
                content_hash=v.content_hash,
                snapshot=_entity_to_response(v.snapshot),
                saved_by=v.saved_by,
                saved_at=v.saved_at,
            ),
        )

    @get("/manifest")
    async def get_version_manifest(
        self,
        state: State,
    ) -> ApiResponse[dict[str, int]]:
        """Get current version manifest for all entities.

        Returns:
            ``ApiResponse[dict[str, int]]`` instance.
        """
        app_state: AppState = state.app_state
        manifest = await app_state.ontology_service.get_version_manifest()
        return ApiResponse(data=manifest)

    # ── Drift Detection ────────────────────────────────────────

    @get("/drift")
    async def list_drift_reports(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[DriftReportResponse]:
        """Get latest drift reports for all entities.

        Returns:
            ``PaginatedResponse[DriftReportResponse]`` instance.
        """
        app_state: AppState = state.app_state
        store = app_state.drift_report_store
        if store is None:
            _, meta = paginate_cursor(
                (),
                limit=limit,
                cursor=cursor,
                secret=app_state.cursor_secret,
            )
            return PaginatedResponse(data=(), pagination=meta)

        # Request ``limit + 1`` so ``paginate_cursor`` can detect that
        # another page exists (its ``has_more`` check compares
        # ``next_offset`` against ``len(items)`` -- if the store only
        # returns ``limit`` items the helper always reports
        # ``has_more=False`` regardless of the true count).
        reports = await store.get_all_latest(limit=limit + 1)
        responses = tuple(_drift_report_to_response(r) for r in reports)
        page, meta = paginate_cursor(
            responses,
            limit=limit,
            cursor=cursor,
            secret=app_state.cursor_secret,
        )
        return PaginatedResponse(data=page, pagination=meta)

    @get("/drift/{entity_name:str}")
    async def get_drift_report(
        self,
        state: State,
        entity_name: PathName,
    ) -> ApiResponse[tuple[DriftReportResponse, ...]]:
        """Get drift reports for a specific entity.

        Returns:
            Result matching the declared return annotation.
        """
        app_state: AppState = state.app_state
        store = app_state.drift_report_store
        if store is None:
            return ApiResponse(data=())

        reports = await store.get_latest(entity_name)
        responses = tuple(_drift_report_to_response(r) for r in reports)
        return ApiResponse(data=responses)

    @post(
        "/drift/check",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("ontology.drift_check", key="user"),
        ],
    )
    async def trigger_drift_check(
        self,
        state: State,
    ) -> ApiResponse[dict[str, str]]:
        """Trigger on-demand drift check for all entities.

        Returns:
            ``ApiResponse[dict[str, str]]`` instance.
        """
        app_state: AppState = state.app_state
        drift_service = app_state.drift_detection_service
        if drift_service is None:
            logger.warning(
                API_REQUEST_ERROR,
                reason="drift_service_unavailable",
            )
            return ApiResponse(
                data={"status": "drift_service_not_configured"},
            )

        # Agent discovery is handled by the engine -- trigger uses
        # empty tuple to signal "check all entities, no agent sample".
        logger.info(ONTOLOGY_DRIFT_CHECK_STARTED, source="api")
        await drift_service.check_all(agent_ids=())
        logger.info(ONTOLOGY_DRIFT_CHECK_COMPLETED, source="api")
        return ApiResponse(data={"status": "drift_check_completed"})

    # ── Admin ──────────────────────────────────────────────────

    @post(
        "/admin/derive",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("ontology.admin_derive", key="user"),
        ],
    )
    async def admin_derive(
        self,
        state: State,
    ) -> ApiResponse[dict[str, int]]:
        """Re-run auto-derivation from decorated models.

        Returns:
            ``ApiResponse[dict[str, int]]`` instance.
        """
        app_state: AppState = state.app_state
        count = await app_state.ontology_service.bootstrap()
        return ApiResponse(data={"derived_count": count})

    @post(
        "/admin/sync-org-memory",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy(
                "ontology.admin_sync_org_memory",
                key="user",
            ),
        ],
    )
    async def admin_sync_org_memory(
        self,
        state: State,
    ) -> ApiResponse[dict[str, int | str]]:
        """Force re-sync all entity definitions to OrgMemory.

        Returns:
            ``ApiResponse[dict[str, int | str]]`` instance.
        """
        app_state: AppState = state.app_state
        sync_service = app_state.ontology_sync_service
        if sync_service is None:
            logger.warning(
                API_REQUEST_ERROR,
                reason="sync_service_unavailable",
            )
            return ApiResponse(
                data={"status": "sync_service_not_configured"},
            )

        count = await sync_service.sync_all()
        logger.info(
            ONTOLOGY_ADMIN_SYNC_COMPLETED,
            published_count=count,
        )
        return ApiResponse(
            data={"status": "sync_completed", "published_count": count},
        )
