# module-kind: controller
"""Ontology entity-definition CRUD controller."""

from datetime import UTC, datetime
from typing import Annotated

from litestar import Controller, delete, get, post, put
from litestar.datastructures import State
from litestar.params import QueryParameter
from litestar.status_codes import HTTP_204_NO_CONTENT

from synthorg.api.controllers.ontology._shared import (
    _DEFAULT_LIMIT,
    _entity_to_response,
    _ontology_service,
)
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.dto_ontology import (
    CreateEntityRequest,
    EntityResponse,
    UpdateEntityRequest,
)
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathName
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.core.domain_errors import NotFoundError, ValidationError
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_REQUEST_ERROR,
    API_RESOURCE_NOT_FOUND,
)
from synthorg.ontology.errors import OntologyDuplicateError, OntologyNotFoundError
from synthorg.ontology.models import (
    EntityDefinition,
    EntityField,
    EntityRelation,
    EntitySource,
    EntityTier,
)

logger = get_logger(__name__)


def _build_entity_updates(
    existing: EntityDefinition,
    data: UpdateEntityRequest,
) -> dict[str, object]:
    """Map a partial update request onto entity field updates.

    ``fields`` / ``relationships`` are mapped only for non-CORE
    entities; the handler rejects CORE mutations before calling this,
    so the tier check here only guards the silent-skip path.

    Returns:
        Mapping of changed field names to their new values; empty when
        the request carries no changes.
    """
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
    return updates


class OntologyController(Controller):
    """Entity definition CRUD for the ontology subsystem."""

    path = "/ontology"
    tags = ("ontology",)
    guards = [require_read_access]  # noqa: RUF012

    @get("/entities")
    async def list_entities(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
        tier: Annotated[
            str | None,
            QueryParameter(
                max_length=QUERY_MAX_LENGTH,
                description="Filter to entity definitions in this tier.",
            ),
        ] = None,
    ) -> PaginatedResponse[EntityResponse]:
        """List all entity definitions, filterable by tier.

        Returns:
            ``PaginatedResponse[EntityResponse]`` instance.

        Raises:
            ValidationError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        svc = _ontology_service(app_state)

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
            secret=cursor_secret_of(app_state),
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
            entity = await _ontology_service(app_state).get(name)
        except OntologyNotFoundError:
            msg = "Entity not found"
            logger.warning(
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
            await _ontology_service(app_state).register(entity)
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
        svc = _ontology_service(app_state)

        try:
            existing = await svc.get(name)
        except OntologyNotFoundError:
            msg = "Entity not found"
            logger.warning(API_RESOURCE_NOT_FOUND, resource="entity", name=name)
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

        updates = _build_entity_updates(existing, data)
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
        svc = _ontology_service(app_state)

        try:
            entity = await svc.get(name)
        except OntologyNotFoundError:
            msg = "Entity not found"
            logger.warning(API_RESOURCE_NOT_FOUND, resource="entity", name=name)
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
