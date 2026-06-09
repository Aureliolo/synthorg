# module-kind: controller
"""Ontology entity-version controller -- history, snapshots, manifest."""

from typing import Annotated

from litestar import Controller, get
from litestar.datastructures import State
from litestar.params import PathParameter

from synthorg.api.controllers.ontology._shared import (
    _DEFAULT_LIMIT,
    _entity_to_response,
    _ontology_service,
)
from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.dto_ontology import EntityVersionResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    encode_countless_seek_meta,
)
from synthorg.api.path_params import PathName
from synthorg.api.state import AppState
from synthorg.core.domain_errors import NotFoundError
from synthorg.ontology.errors import OntologyNotFoundError


class OntologyVersionsController(Controller):
    """Entity-definition version history and manifest."""

    path = "/ontology"
    tags = ("ontology",)
    guards = [require_read_access]  # noqa: RUF012

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
        svc = _ontology_service(app_state)

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
            else decode_cursor(cursor, secret=cursor_secret_of(app_state))
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
            secret=cursor_secret_of(app_state),
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
        svc = _ontology_service(app_state)

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
        manifest = await _ontology_service(app_state).get_version_manifest()
        return ApiResponse(data=manifest)
