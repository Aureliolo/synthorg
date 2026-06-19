# module-kind: controller
"""Security-configuration export / import settings endpoints."""

from datetime import UTC, datetime

from litestar import Controller, Request, get, post
from litestar.datastructures import State
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from synthorg._core.features import require_service
from synthorg.api.auth.controller_helpers import require_authenticated_user
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_ceo_or_manager, require_read_access
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.core.boundary import parse_typed
from synthorg.core.domain_errors import ValidationError as DomainValidationError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_SECURITY_CONFIG_EXPORTED,
    API_SECURITY_CONFIG_IMPORT_FAILED,
    API_SECURITY_CONFIG_IMPORTED,
)
from synthorg.observability.events.security import SECURITY_SETTINGS_IMPORTED
from synthorg.observability.events.settings import SETTINGS_NOT_FOUND
from synthorg.security.config import SecurityConfig
from synthorg.settings.enums import SettingNamespace, SettingsImportSource
from synthorg.settings.service_protocol import SettingsServiceProtocol
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)

_SECURITY_SETTING_FIELDS: dict[str, str] = {
    "enabled": "enabled",
    "audit_enabled": "audit_enabled",
    "post_tool_scanning_enabled": "post_tool_scanning_enabled",
    "output_scan_policy_type": "output_scan_policy_type",
}
"""Maps ``SecurityConfig`` field names to setting keys."""


class SecurityConfigExportResponse(BaseModel):
    """Exported security configuration with metadata."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    config: dict[str, object]
    exported_at: AwareDatetime
    custom_policies_warning: NotBlankStr | None = None


class SecurityConfigImportRequest(BaseModel):
    """Request body to import a security configuration."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    config: dict[str, object]


async def _persist_security_settings(
    svc: SettingsServiceProtocol,
    config: SecurityConfig,
    *,
    import_source: SettingsImportSource,
) -> int:
    """Persist registered security settings from a validated config.

    Only fields with a registered setting definition are persisted.
    Code-defined fields (custom_policies, rule_engine, etc.) are
    not persistable through the settings service.

    The persist runs through ``svc.set_many`` so the whole batch
    commits or rolls back together; per-key ``svc.set`` would leave
    the system in a mixed configuration if a later key failed
    validation after earlier keys already landed.

    Args:
        svc: Settings service for persistence.
        config: Validated security configuration.
        import_source: Source attribution forwarded so audit logs
            distinguish bulk-import writes from per-key
            ``PATCH /settings`` calls.

    Returns:
        The number of registered settings persisted (so the caller can
        stamp the audit envelope with an accurate ``key_count``).
    """
    ns = SettingNamespace.SECURITY
    items: list[tuple[str, str, str]] = []
    for field_name, key in _SECURITY_SETTING_FIELDS.items():
        # Skip fields that aren't registered as persistable settings;
        # the original loop probed each key with ``svc.set`` and ate
        # ``SettingNotFoundError`` -- mirror that here so a config
        # carrying a code-defined field doesn't fail the import.
        if svc.registry.get(ns.value, key) is None:
            logger.debug(SETTINGS_NOT_FOUND, namespace=ns.value, key=key)
            continue
        value = getattr(config, field_name)
        str_value = str(value).lower() if isinstance(value, bool) else str(value)
        items.append((ns.value, key, str_value))
    if not items:
        return 0
    # ``expected_updated_at_map`` of all-empty strings means
    # "first-write" semantics for every key; matches the prior
    # ``svc.set`` loop, which also did not pass CAS versions.
    await svc.set_many(
        items,
        expected_updated_at_map={(ns_val, key): "" for ns_val, key, _ in items},
        import_source=import_source,
    )
    return len(items)


class SettingsSecurityController(Controller):
    """Export and import the security configuration."""

    path = "/settings"
    tags = ("settings",)
    guards = [require_read_access]  # noqa: RUF012

    @get("/security/export")
    async def export_security_config(
        self,
        state: State,
    ) -> ApiResponse[SecurityConfigExportResponse]:
        """Export the current security configuration as JSON.

        Returns:
            Security config dump with export timestamp.
        """
        app_state: AppState = state.app_state
        security_cfg = app_state.config.security
        dumped = security_cfg.model_dump(mode="json")
        warning = (
            "custom_policies are code-defined Pydantic models; "
            "they export as data but may require matching "
            "Python code on import"
            if security_cfg.custom_policies
            else None
        )
        logger.info(API_SECURITY_CONFIG_EXPORTED)
        return ApiResponse(
            data=SecurityConfigExportResponse(
                config=dumped,
                exported_at=datetime.now(UTC),
                custom_policies_warning=warning,
            ),
        )

    @post(
        "/security/import",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("settings.import", key="user"),
        ],
    )
    async def import_security_config(
        self,
        state: State,
        data: SecurityConfigImportRequest,
        request: Request[object, object, State],
    ) -> ApiResponse[SecurityConfigExportResponse]:
        """Import, validate, and persist a security configuration.

        Validates the payload as a ``SecurityConfig``, then persists
        each field that has a registered setting through the
        :class:`SettingsService`. After the write lands, emits a single
        signed ``SECURITY_SETTINGS_IMPORTED`` envelope correlating the
        batch to its actor so the audit chain records the bulk import as
        one control-plane action (the per-key ``SECURITY_SETTINGS_CHANGED``
        emissions from ``set_many`` are grouped under it).

        Args:
            state: Application state.
            data: Import request with config dict.
            request: Incoming request, carrying the authenticated actor.

        Returns:
            The validated and persisted config.

        Raises:
            UnauthorizedError: If no authenticated actor is on the request.
            DomainValidationError: If the config fails validation
                (HTTP 422).
            ValidationError: Generic schema-level validation rejected
                a registered setting value.
        """
        app_state: AppState = state.app_state
        # The class guard already enforces CEO/manager, but resolve the
        # identity fail-closed rather than via a bare ``scope["user"]`` key
        # access that would 500 if the guard chain were ever reordered.
        actor = require_authenticated_user(request)
        try:
            validated = parse_typed(
                "settings.security",
                data.config,
                SecurityConfig,
            )
        except ValidationError as exc:
            # Redact: ``str(exc)`` (and the f-string substitution below)
            # would surface rejected input values from the import payload,
            # which can hold secrets or operator-sensitive configuration.
            # The 422 response keeps a generic message; full diagnostic
            # detail stays on the server-side warning stream via
            # ``safe_error_description`` so credential text never reaches
            # the response or operator-readable logs.
            logger.warning(
                API_SECURITY_CONFIG_IMPORT_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Invalid security config"
            raise DomainValidationError(msg) from exc

        key_count = await _persist_security_settings(
            require_service(
                app_state.slice(SettingsStateSlice).settings_service, "Settings Service"
            ),
            validated,
            import_source=SettingsImportSource.API_BODY,
        )
        logger.info(
            SECURITY_SETTINGS_IMPORTED,
            principal=str(actor.user_id),
            key_count=key_count,
            import_source=SettingsImportSource.API_BODY.value,
        )

        warning = (
            "custom_policies are code-defined Pydantic models; "
            "they export as data but may require matching "
            "Python code on import"
            if validated.custom_policies
            else None
        )
        logger.info(API_SECURITY_CONFIG_IMPORTED)
        return ApiResponse(
            data=SecurityConfigExportResponse(
                config=validated.model_dump(mode="json"),
                exported_at=datetime.now(UTC),
                custom_policies_warning=warning,
            ),
        )
