"""Custom signal rule CRUD controller.

Provides API endpoints for creating, reading, updating, and
deleting user-defined declarative rules, plus a preview endpoint
for dry-run evaluation.
"""

from datetime import UTC, datetime
from typing import Final

from litestar import Controller, delete, get, patch, post
from litestar.datastructures import State
from litestar.status_codes import HTTP_204_NO_CONTENT

from synthorg.api.controllers._custom_rules_helpers import (
    CreateCustomRuleRequest,
    PreviewRuleRequest,
    UpdateCustomRuleRequest,
    _build_preview_snapshot,
    _metric_to_dict,
    rule_to_dict,
)
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.core.domain_errors import ConflictError, NotFoundError
from synthorg.core.persistence_errors import ConstraintViolationError
from synthorg.core.types import NotBlankStr
from synthorg.meta.models import ProposalAltitude, RuleSeverity
from synthorg.meta.rules.custom import (
    METRIC_REGISTRY,
    CustomRuleDefinition,
    DeclarativeRule,
)
from synthorg.meta.rules.service import CustomRulesService
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_RESOURCE_CONFLICT,
    API_RESOURCE_NOT_FOUND,
)
from synthorg.observability.events.security import (
    SECURITY_CUSTOM_RULE_CREATED,
    SECURITY_CUSTOM_RULE_DELETED,
    SECURITY_CUSTOM_RULE_TOGGLED,
    SECURITY_CUSTOM_RULE_UPDATED,
)
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


def _service(state: State) -> CustomRulesService:
    """Build the per-request :class:`CustomRulesService`.

    Returns:
        ``CustomRulesService`` instance.
    """
    return CustomRulesService(repo=persistence_of(state.app_state).custom_rules)


# ── Controller ────────────────────────────────────────────────────


class CustomRuleController(Controller):
    """CRUD endpoints for custom declarative signal rules.

    All endpoints are under ``/meta/custom-rules`` (the app router
    adds the ``/api/v1`` prefix).
    """

    path = "/meta/custom-rules"
    tags = ["meta"]  # noqa: RUF012
    guards = [require_read_access]  # noqa: RUF012

    @get("/")
    async def list_rules(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[dict[str, object]]:
        """List all custom rules (paginated).

        Args:
            state: Application state.
            cursor: Opaque pagination cursor from the previous page;
                ``None`` starts at the beginning.
            limit: Page size.

        Returns:
            Paginated custom rule definitions.
        """
        rules, _total = await _service(state).list_rules()
        entries = tuple(rule_to_dict(r) for r in rules)
        page, meta = paginate_cursor(
            entries,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(state.app_state),
        )
        return PaginatedResponse[dict[str, object]](data=page, pagination=meta)

    @get("/{rule_id:str}")
    async def get_rule(
        self,
        state: State,
        rule_id: PathId,
    ) -> ApiResponse[dict[str, object]]:
        """Get a single custom rule.

        Args:
            state: Litestar application state.
            rule_id: UUID of the rule.

        Returns:
            The custom rule definition.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
        """
        rule = await _service(state).get(rule_id)
        if rule is None:
            msg = f"Custom rule {rule_id} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="custom_rule",
                rule_id=rule_id,
                operation="read",
            )
            raise NotFoundError(msg)
        return ApiResponse[dict[str, object]](data=rule_to_dict(rule))

    @post(
        "/",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("custom_rules.create", key="user"),
        ],
        status_code=201,
    )
    async def create_rule(
        self,
        state: State,
        data: CreateCustomRuleRequest,
    ) -> ApiResponse[dict[str, object]]:
        """Create a new custom rule.

        Args:
            state: Litestar application state.
            data: Rule creation request.

        Returns:
            The created rule definition.

        Raises:
            ConflictError: Raised on the corresponding failure path.
        """
        now = datetime.now(UTC)
        definition = CustomRuleDefinition(
            name=data.name,
            description=data.description,
            metric_path=data.metric_path,
            comparator=data.comparator,
            threshold=data.threshold,
            severity=data.severity,
            target_altitudes=data.target_altitudes,
            created_at=now,
            updated_at=now,
        )
        try:
            saved = await _service(state).create(definition)
        except ConstraintViolationError as exc:
            logger.warning(
                API_RESOURCE_CONFLICT,
                resource="custom_rule",
                operation="create",
                name=data.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConflictError(str(exc)) from exc
        # Custom rules define automation triggers (control plane);
        # route the success event through the audit chain via the
        # SECURITY_* prefix so the mutation is signed and chained
        # alongside settings / autonomy changes. ``rule`` is the bare
        # canonical identifier (UUID), mirroring the
        # ``SECURITY_PROVIDER_CREATED`` naming pattern; the human name
        # rides alongside as ``rule_name`` for readability.
        logger.info(
            SECURITY_CUSTOM_RULE_CREATED,
            rule=str(saved.id),
            rule_name=saved.name,
            metric_path=saved.metric_path,
            severity=saved.severity.value,
        )
        return ApiResponse[dict[str, object]](
            data=rule_to_dict(saved),
        )

    @patch(
        "/{rule_id:str}",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("custom_rules.update", key="user"),
        ],
    )
    async def update_rule(
        self,
        state: State,
        rule_id: PathId,
        data: UpdateCustomRuleRequest,
    ) -> ApiResponse[dict[str, object]]:
        """Update an existing custom rule.

        Args:
            state: Litestar application state.
            rule_id: UUID of the rule to update.
            data: Partial update request.

        Returns:
            The updated rule definition.

        Raises:
            ConflictError: Raised on the corresponding failure path.
        """
        # ``CustomRuleNotFoundError`` inherits ``NotFoundError`` so
        # the central handler maps it to 404 directly; the previous
        # controller-level ``raise NotFoundError(str(exc))`` collapsed
        # the type and lost the discriminating envelope.
        try:
            updated = await _service(state).update(
                NotBlankStr(rule_id),
                data.model_dump(exclude_none=True),
            )
        except ConstraintViolationError as exc:
            logger.warning(
                API_RESOURCE_CONFLICT,
                resource="custom_rule",
                operation="update",
                rule_id=rule_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConflictError(str(exc)) from exc
        logger.info(
            SECURITY_CUSTOM_RULE_UPDATED,
            rule=rule_id,
            fields_changed=sorted(data.model_dump(exclude_none=True).keys()),
        )
        return ApiResponse[dict[str, object]](
            data=rule_to_dict(updated),
        )

    @delete(
        "/{rule_id:str}",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("custom_rules.delete", key="user"),
        ],
        status_code=HTTP_204_NO_CONTENT,
    )
    async def delete_rule(
        self,
        state: State,
        rule_id: PathId,
    ) -> None:
        """Delete a custom rule.

        Args:
            state: Litestar application state.
            rule_id: UUID of the rule to delete.
        """
        # ``CustomRuleNotFoundError`` propagates with its inherited
        # ``NotFoundError`` envelope.
        await _service(state).delete(NotBlankStr(rule_id))
        logger.info(
            SECURITY_CUSTOM_RULE_DELETED,
            rule=rule_id,
        )

    @post(
        "/{rule_id:str}/toggle",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("custom_rules.toggle", key="user"),
        ],
    )
    async def toggle_rule(
        self,
        state: State,
        rule_id: PathId,
    ) -> ApiResponse[dict[str, object]]:
        """Toggle a custom rule's enabled status.

        Args:
            state: Litestar application state.
            rule_id: UUID of the rule to toggle.

        Returns:
            The updated rule definition.
        """
        # ``CustomRuleNotFoundError`` propagates with its inherited
        # ``NotFoundError`` envelope.
        toggled = await _service(state).toggle(NotBlankStr(rule_id))
        logger.info(
            SECURITY_CUSTOM_RULE_TOGGLED,
            rule=rule_id,
            enabled=toggled.enabled,
        )
        return ApiResponse[dict[str, object]](
            data=rule_to_dict(toggled),
        )

    @get("/metrics")
    async def list_metrics(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[dict[str, object]]:
        """List available snapshot metrics for rule building (paginated).

        Returns metric descriptors with bounds and metadata. The
        registry is bounded today but the endpoint is paginated for
        uniform shape with the rest of the list surface.

        Returns:
            ``PaginatedResponse[dict[str, object]]`` instance.
        """
        entries = tuple(_metric_to_dict(m) for m in METRIC_REGISTRY)
        page, meta = paginate_cursor(
            entries,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(state.app_state),
        )
        return PaginatedResponse[dict[str, object]](data=page, pagination=meta)

    @post(
        "/preview",
        guards=[
            per_op_rate_limit_from_policy("custom_rules.preview", key="user"),
        ],
    )
    async def preview_rule(
        self,
        data: PreviewRuleRequest,
    ) -> ApiResponse[dict[str, object]]:
        """Dry-run a rule definition against a sample metric value.

        Args:
            data: Preview request with rule definition and sample.

        Returns:
            Whether the rule would fire and the match details.
        """
        now = datetime.now(UTC)
        definition = CustomRuleDefinition(
            name="preview",
            description="Preview rule",
            metric_path=data.metric_path,
            comparator=data.comparator,
            threshold=data.threshold,
            severity=RuleSeverity.INFO,
            target_altitudes=(ProposalAltitude.CONFIG_TUNING,),
            created_at=now,
            updated_at=now,
        )
        rule = DeclarativeRule(definition)

        # Build a snapshot with the sample value injected.
        snapshot = _build_preview_snapshot(
            data.metric_path,
            data.sample_value,
        )
        match = rule.evaluate(snapshot)
        result: dict[str, object] = {
            "would_fire": match is not None,
            "match": None,
        }
        if match is not None:
            result["match"] = {
                "rule_name": match.rule_name,
                "severity": match.severity.value,
                "description": match.description,
                "signal_context": match.signal_context,
            }
        return ApiResponse[dict[str, object]](data=result)
