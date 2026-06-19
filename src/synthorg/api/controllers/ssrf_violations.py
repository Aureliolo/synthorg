# module-kind: controller
"""SSRF-violation review controller.

Exposes the previously write-never ``SsrfViolation`` store so operators
can review URLs the outbound SSRF guard blocked and allow / deny each
one. Endpoints under ``/providers/ssrf-violations``:

* ``GET /`` -- list violations (optional status filter, paginated).
* ``POST /{id}/resolve`` -- allow or deny a pending violation.
"""

from datetime import UTC, datetime
from typing import Annotated, Final

from litestar import Controller, get, post
from litestar.datastructures import State
from litestar.params import QueryParameter

from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.dto_ssrf import ResolveSsrfViolationRequest, SsrfViolationDTO
from synthorg.api.guards import require_ceo_or_manager, require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    encode_countless_seek_meta,
)
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.services.ssrf_violation_service import SsrfViolationService
from synthorg.api.state import AppState
from synthorg.core.actor_context import resolve_decided_by
from synthorg.core.domain_errors import ResourceNotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.state import persistence_of
from synthorg.security.ssrf_violation import SsrfViolationStatus

_DEFAULT_LIMIT: Final[int] = 50


def _service(state: State) -> SsrfViolationService:
    """Build the SSRF-violation service over the wired repository.

    Returns:
        A service bound to ``persistence.ssrf_violations``.
    """
    app_state: AppState = state.app_state
    return SsrfViolationService(repo=persistence_of(app_state).ssrf_violations)


class SsrfViolationController(Controller):
    """Operator review surface for SSRF-blocked outbound URLs."""

    path = "/providers/ssrf-violations"
    tags = ("providers", "ssrf-violations")
    guards = (require_read_access,)

    @get("/")
    async def list_violations(
        self,
        state: State,
        status: Annotated[SsrfViolationStatus | None, QueryParameter()] = None,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[SsrfViolationDTO]:
        """List recorded SSRF violations, optionally filtered by status.

        Args:
            state: Application state.
            status: Optional status filter (pending / allowed / denied).
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            Paginated violations, newest-first.
        """
        service = _service(state)
        secret = cursor_secret_of(state.app_state)
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        # Fetch ``limit + 1`` so the next-page flag comes from a bounded
        # window instead of materialising every violation row first.
        rows = await service.list_violations(
            status=status, limit=limit + 1, offset=offset
        )
        meta = encode_countless_seek_meta(
            offset=offset,
            fetched_rows=len(rows),
            limit=limit,
            secret=secret,
        )
        entries = tuple(SsrfViolationDTO.from_entity(r) for r in rows[:limit])
        return PaginatedResponse(data=entries, pagination=meta)

    @post(
        "/{violation_id:str}/resolve",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("security.ssrf_resolve", key="user"),
        ],
    )
    async def resolve_violation(
        self,
        violation_id: PathId,
        data: ResolveSsrfViolationRequest,
        state: State,
    ) -> ApiResponse[SsrfViolationDTO]:
        """Allow or deny a pending SSRF violation.

        The deciding operator is taken from the authenticated session
        (never the request body), so the audit trail cannot be forged.
        ``ResolveSsrfViolationRequest.status`` is constrained to
        ``allowed`` / ``denied`` at the type boundary, so a ``pending``
        decision is rejected with a structured 422 before this handler
        runs.

        Returns:
            The resolved violation.

        Raises:
            ResourceNotFoundError: When no pending violation matches the id.
        """
        service = _service(state)
        updated = await service.update_status(
            NotBlankStr(violation_id),
            status=data.status,
            resolved_by=NotBlankStr(resolve_decided_by()),
            resolved_at=datetime.now(UTC),
        )
        if not updated:
            msg = f"No pending SSRF violation found for id {violation_id!r}"
            raise ResourceNotFoundError(msg)
        violation = await service.get(NotBlankStr(violation_id))
        if violation is None:  # pragma: no cover -- just-updated row must exist
            msg = f"SSRF violation {violation_id!r} vanished after resolution"
            raise ResourceNotFoundError(msg)
        return ApiResponse(data=SsrfViolationDTO.from_entity(violation))
