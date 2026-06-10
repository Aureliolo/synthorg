# module-kind: controller
"""Workflow blueprint controller -- listing and instantiation."""

import asyncio
from datetime import UTC, datetime

from litestar import Controller, Response, get, post
from litestar.datastructures import State

from synthorg.api.auth import get_authenticated_user_id
from synthorg.api.controllers._workflow_builders import (
    build_definition_from_blueprint,
    load_blueprint_or_raise,
)
from synthorg.api.controllers.workflows._shared import _service
from synthorg.api.dto import ApiResponse
from synthorg.api.dto_workflow import (
    BlueprintInfoResponse,
    CreateFromBlueprintRequest,
)
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.engine.workflow.blueprint_loader import list_blueprints
from synthorg.engine.workflow.definition import WorkflowDefinition
from synthorg.engine.workflow.enums import WorkflowType
from synthorg.observability import get_logger
from synthorg.observability.events.blueprint import (
    BLUEPRINT_INSTANTIATE_START,
    BLUEPRINT_INSTANTIATE_SUCCESS,
)
from synthorg.observability.metrics_hub import record_blueprint_instantiation

logger = get_logger(__name__)


class WorkflowBlueprintController(Controller):
    """Workflow blueprint listing and instantiation."""

    path = "/workflows"
    tags = ("workflows",)

    @get("/blueprints", guards=[require_read_access])
    async def list_workflow_blueprints(
        self,
    ) -> Response[ApiResponse[tuple[BlueprintInfoResponse, ...]]]:
        """List available workflow blueprints.

        Returns:
            Result matching the declared return annotation.
        """
        infos = await asyncio.to_thread(list_blueprints)
        responses = tuple(
            BlueprintInfoResponse(
                name=i.name,
                display_name=i.display_name,
                description=i.description,
                source=i.source,
                tags=i.tags,
                workflow_type=WorkflowType(i.workflow_type),
                node_count=i.node_count,
                edge_count=i.edge_count,
            )
            for i in infos
        )
        return Response(
            content=ApiResponse[tuple[BlueprintInfoResponse, ...]](
                data=responses,
            ),
        )

    @post(
        "/from-blueprint",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy(
                "workflows.create_from_blueprint",
                key="user",
            ),
        ],
    )
    async def create_from_blueprint(
        self,
        state: State,
        data: CreateFromBlueprintRequest,
    ) -> Response[ApiResponse[WorkflowDefinition]]:
        """Create a new workflow definition from a blueprint.

        Returns:
            Result matching the declared return annotation.
        """
        creator = get_authenticated_user_id()
        logger.info(
            BLUEPRINT_INSTANTIATE_START,
            blueprint_name=data.blueprint_name,
        )

        bp = await load_blueprint_or_raise(data.blueprint_name)

        now = datetime.now(UTC)
        definition = build_definition_from_blueprint(
            bp,
            data,
            creator,
            now,
        )

        await _service(state).create_definition(definition, saved_by=creator)

        # Snapshot orchestration moved into ``WorkflowService.create_definition``
        # (via the ``saved_by`` kwarg), so no explicit ``snapshot_if_changed``
        # call is needed here.
        logger.info(
            BLUEPRINT_INSTANTIATE_SUCCESS,
            definition_id=str(definition.id),
            blueprint_name=data.blueprint_name,
        )
        record_blueprint_instantiation(
            outcome="success",
            blueprint_name=data.blueprint_name,
        )
        return Response(
            content=ApiResponse[WorkflowDefinition](data=definition),
            status_code=201,
        )
