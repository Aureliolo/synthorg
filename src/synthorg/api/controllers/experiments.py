"""A/B experiment registry endpoints.

Mounts the variant CRUD plus deterministic assignment lookup under
``/api/v1/experiments``. Variant lifecycle is operator-facing; the
assignment endpoint is the runtime path agents call when they want to
discover which experiment branch they belong to.
"""

from typing import Final

from litestar import Controller, get, post
from litestar.datastructures import State

from synthorg.api.cursor import decode_cursor, encode_cursor
from synthorg.api.dto import (
    ApiResponse,
    AssignExperimentRequest,
    PaginatedResponse,
    PaginationMeta,
    RegisterExperimentVariantRequest,
)
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.core.types import NotBlankStr
from synthorg.experiments.models import (
    ExperimentAssignment,
    ExperimentVariant,
)
from synthorg.meta.state import experiment_service_of
from synthorg.observability import get_logger

logger = get_logger(__name__)

_DEFAULT_LIMIT: Final[int] = 50


class ExperimentsController(Controller):
    """REST surface for the experiment registry."""

    path = "/experiments"
    tags = ("experiments",)
    guards = [require_read_access]  # noqa: RUF012

    @get("/{experiment:str}/variants")
    async def list_variants(
        self,
        state: State,
        experiment: PathId,
        limit: CursorLimit = _DEFAULT_LIMIT,
        cursor: CursorParam = None,
    ) -> PaginatedResponse[ExperimentVariant]:
        """List registered variants for an experiment (cursor-paginated).

        Returns:
            ``PaginatedResponse[ExperimentVariant]`` instance.
        """
        app_state: AppState = state.app_state
        variants = await experiment_service_of(app_state).list_variants(
            NotBlankStr(experiment),
        )
        page, meta = paginate_cursor(
            variants,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        return PaginatedResponse(data=page, pagination=meta)

    @post(
        "/{experiment:str}/variants",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("experiments.register", key="user"),
        ],
        status_code=201,
    )
    async def register_variant(
        self,
        state: State,
        experiment: PathId,
        data: RegisterExperimentVariantRequest,
    ) -> ApiResponse[ExperimentVariant]:
        """Register or replace a variant on an experiment.

        API-only: variant registration is a deployment / scripting
        operation and is intentionally not surfaced in the dashboard,
        which exposes only the read views (variants + assignments).

        Returns:
            ``ApiResponse[ExperimentVariant]`` instance.
        """
        app_state: AppState = state.app_state
        record = await experiment_service_of(app_state).register_variant(
            experiment=NotBlankStr(experiment),
            variant=data.variant,
            weight=data.weight,
            description=data.description,
        )
        return ApiResponse(data=record)

    @post(
        "/{experiment:str}/assign",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("experiments.assign", key="user"),
        ],
    )
    async def assign(
        self,
        state: State,
        experiment: PathId,
        data: AssignExperimentRequest,
    ) -> ApiResponse[ExperimentAssignment]:
        """Return the deterministic variant assignment for a subject.

        On first call for ``(experiment, subject_id)`` the service
        computes the assignment and persists it; subsequent calls
        return the recorded assignment unchanged.

        API-only: this is the runtime path agents call to discover their
        branch; it is not surfaced in the dashboard (the dashboard only
        reads recorded assignments).

        Returns:
            ``ApiResponse[ExperimentAssignment]`` instance.
        """
        app_state: AppState = state.app_state
        assignment = await experiment_service_of(app_state).assign(
            experiment=NotBlankStr(experiment),
            subject_id=data.subject_id,
        )
        return ApiResponse(data=assignment)

    @get("/{experiment:str}/assignments")
    async def list_assignments(
        self,
        state: State,
        experiment: PathId,
        limit: CursorLimit = _DEFAULT_LIMIT,
        cursor: CursorParam = None,
    ) -> PaginatedResponse[ExperimentAssignment]:
        """List recorded assignments for an experiment (newest first).

        Pagination uses the standard opaque HMAC-signed cursor (see
        :mod:`synthorg.api.cursor`); the cursor decodes to an internal
        offset so callers cannot forge a token that skips to an
        arbitrary page.

        Returns:
            ``PaginatedResponse[ExperimentAssignment]`` instance.
        """
        app_state: AppState = state.app_state
        offset = (
            decode_cursor(cursor, secret=cursor_secret_of(app_state)) if cursor else 0
        )
        page, total = await experiment_service_of(app_state).list_assignments(
            NotBlankStr(experiment),
            limit=limit,
            offset=offset,
        )
        next_offset = offset + len(page)
        has_more = next_offset < total
        meta = PaginationMeta(
            limit=limit,
            next_cursor=(
                encode_cursor(next_offset, secret=cursor_secret_of(app_state))
                if has_more
                else None
            ),
            has_more=has_more,
        )
        return PaginatedResponse(data=page, pagination=meta)
