"""A/B experiment registry endpoints.

Mounts the variant CRUD plus deterministic assignment lookup under
``/api/v1/experiments``. Variant lifecycle is operator-facing; the
assignment endpoint is the runtime path agents call when they want to
discover which experiment branch they belong to.
"""

from typing import Final

from litestar import Controller, get, post
from litestar.datastructures import State  # noqa: TC002

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
    CursorLimit,  # noqa: TC001 -- runtime parameter annotation
    CursorParam,  # noqa: TC001 -- runtime parameter annotation
)
from synthorg.api.path_params import PathId  # noqa: TC001
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.types import NotBlankStr
from synthorg.experiments.models import (  # noqa: TC001 -- runtime return-type annotations
    ExperimentAssignment,
    ExperimentVariant,
)
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
    ) -> ApiResponse[tuple[ExperimentVariant, ...]]:
        """List every registered variant for an experiment."""
        app_state: AppState = state.app_state
        variants = await app_state.experiment_service.list_variants(
            NotBlankStr(experiment),
        )
        return ApiResponse(data=variants)

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
        """Register or replace a variant on an experiment."""
        app_state: AppState = state.app_state
        record = await app_state.experiment_service.register_variant(
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
        """
        app_state: AppState = state.app_state
        assignment = await app_state.experiment_service.assign(
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
        """
        app_state: AppState = state.app_state
        offset = decode_cursor(cursor, secret=app_state.cursor_secret) if cursor else 0
        page, total = await app_state.experiment_service.list_assignments(
            NotBlankStr(experiment),
            limit=limit,
            offset=offset,
        )
        next_offset = offset + len(page)
        has_more = next_offset < total
        meta = PaginationMeta(
            limit=limit,
            next_cursor=(
                encode_cursor(next_offset, secret=app_state.cursor_secret)
                if has_more
                else None
            ),
            has_more=has_more,
        )
        return PaginatedResponse(data=page, pagination=meta)
