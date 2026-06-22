# module-kind: controller
"""SecOps risk-tier override controller -- CEO-only create/revoke/list.

Runtime overrides reclassify an action type's risk tier, changing how the
tiered approval-timeout policy treats its pending approvals. Each override
has a mandatory expiry and is an immutable audit artefact; revocation is a
state transition. Available only when a tiered approval-timeout policy is
configured (the sole consumer of the risk classifier).
"""

from litestar import Controller, Request, get, post
from litestar.datastructures import State
from pydantic import AwareDatetime, BaseModel, ConfigDict

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_ceo
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.domain_errors import NotFoundError, UnauthorizedError
from synthorg.core.types import NotBlankStr
from synthorg.security.rules.risk_override import RiskTierOverride
from synthorg.security.state import risk_override_service_of


def _require_actor(request: Request[object, object, State]) -> AuthenticatedUser:
    """Return the authenticated actor or reject the request.

    Returns:
        The authenticated actor whose id stamps ``created_by`` /
        ``revoked_by`` on the override audit artefact.

    Raises:
        UnauthorizedError: If no authenticated actor is on the request.
    """
    actor = request.scope.get("user")
    if not isinstance(actor, AuthenticatedUser):
        msg = "No authenticated actor on request"
        raise UnauthorizedError(msg)
    return actor


def _override_to_dict(override: RiskTierOverride) -> dict[str, object]:
    """Render an override as a JSON-safe dict.

    Returns:
        Mapping of the override's public fields.
    """
    return {
        "id": str(override.id),
        "action_type": str(override.action_type),
        "original_tier": override.original_tier.value,
        "override_tier": override.override_tier.value,
        "reason": str(override.reason),
        "created_by": str(override.created_by),
        "created_at": override.created_at.isoformat(),
        "expires_at": override.expires_at.isoformat(),
        "revoked_at": (
            override.revoked_at.isoformat() if override.revoked_at else None
        ),
        "revoked_by": str(override.revoked_by) if override.revoked_by else None,
    }


class CreateRiskOverrideRequest(BaseModel):
    """Request body for creating a risk-tier override."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    action_type: NotBlankStr
    override_tier: ApprovalRiskLevel
    reason: NotBlankStr
    expires_at: AwareDatetime


class RiskOverrideController(Controller):
    """CEO-only endpoints for SecOps risk-tier overrides.

    All endpoints are under ``/security/risk-overrides`` (the app router
    adds the ``/api/v1`` prefix).
    """

    path = "/security/risk-overrides"
    tags = ("security",)
    guards = [require_ceo]  # noqa: RUF012

    @get(
        "/",
        guards=[
            per_op_rate_limit_from_policy("security.risk_override_list", key="user"),
        ],
    )
    async def list_overrides(
        self, state: State
    ) -> ApiResponse[list[dict[str, object]]]:
        """List currently active risk-tier overrides.

        Args:
            state: Application state.

        Returns:
            The active (non-expired, non-revoked) overrides.
        """
        service = risk_override_service_of(state.app_state)
        active = service.list_active()
        return ApiResponse(data=[_override_to_dict(o) for o in active])

    @post(
        "/",
        status_code=201,
        guards=[
            per_op_rate_limit_from_policy("security.risk_override_create", key="user"),
        ],
    )
    async def create_override(
        self,
        state: State,
        request: Request[object, object, State],
        data: CreateRiskOverrideRequest,
    ) -> ApiResponse[dict[str, object]]:
        """Create and apply a risk-tier override.

        Args:
            state: Application state.
            request: Incoming request, carrying the authenticated actor.
            data: Override creation payload.

        Returns:
            The created override (HTTP 201).

        Raises:
            ConflictError: If the override would not change the tier.
        """
        actor = _require_actor(request)
        service = risk_override_service_of(state.app_state)
        override = await service.create(
            action_type=data.action_type,
            override_tier=data.override_tier,
            reason=data.reason,
            created_by=NotBlankStr(str(actor.user_id)),
            expires_at=data.expires_at,
        )
        return ApiResponse(data=_override_to_dict(override))

    @post(
        "/{override_id:str}/revoke",
        guards=[
            per_op_rate_limit_from_policy("security.risk_override_revoke", key="user"),
        ],
    )
    async def revoke_override(
        self,
        state: State,
        request: Request[object, object, State],
        override_id: PathId,
    ) -> ApiResponse[dict[str, object]]:
        """Revoke an active risk-tier override.

        Args:
            state: Application state.
            request: Incoming request, carrying the authenticated actor.
            override_id: Identifier of the override to revoke.

        Returns:
            The revoked override.

        Raises:
            NotFoundError: If no active override with that id exists.
        """
        actor = _require_actor(request)
        service = risk_override_service_of(state.app_state)
        revoked = await service.revoke(
            NotBlankStr(override_id),
            revoked_by=NotBlankStr(str(actor.user_id)),
        )
        if revoked is None:
            msg = f"No active risk override {override_id}"
            raise NotFoundError(msg)
        return ApiResponse(data=_override_to_dict(revoked))


__all__ = ["CreateRiskOverrideRequest", "RiskOverrideController"]
