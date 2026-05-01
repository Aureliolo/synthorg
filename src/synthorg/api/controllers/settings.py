"""Settings controller -- CRUD for runtime-editable settings."""

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Self

from litestar import Controller, Request, Response, delete, get, post, put
from litestar.datastructures import State  # noqa: TC002
from litestar.exceptions import InternalServerException
from litestar.status_codes import HTTP_204_NO_CONTENT
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from synthorg.api.concurrency import check_if_match, compute_etag
from synthorg.api.cursor import decode_keyset_cursor
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_ceo_or_manager, require_read_access
from synthorg.api.pagination import CursorLimit, CursorParam, encode_keyset_meta
from synthorg.api.path_params import PathKey, PathNamespace  # noqa: TC001
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.domain_errors import ValidationError as DomainValidationError
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.config import DEFAULT_SINKS, SinkConfig
from synthorg.observability.enums import LogLevel, SinkType
from synthorg.observability.events.api import (
    API_SECURITY_CONFIG_EXPORTED,
    API_SECURITY_CONFIG_IMPORT_FAILED,
    API_SECURITY_CONFIG_IMPORTED,
)
from synthorg.observability.events.settings import (
    SETTINGS_ENCRYPTION_ERROR,
    SETTINGS_NOT_FOUND,
    SETTINGS_OBSERVABILITY_VALIDATION_FAILED,
    SETTINGS_VALIDATION_FAILED,
)
from synthorg.observability.sink_config_builder import (
    CONSOLE_SINK_ID,
    DEFAULT_FILE_PATHS,
    SinkBuildResult,
    build_log_config_from_settings,
)
from synthorg.security.config import SecurityConfig
from synthorg.settings.enums import SettingNamespace, SettingsImportSource
from synthorg.settings.errors import (
    SettingNotFoundError,
    SettingsEncryptionError,
    SettingValidationError,
)
from synthorg.settings.models import SettingDefinition, SettingEntry  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

_VALID_NAMESPACES: frozenset[str] = frozenset(ns.value for ns in SettingNamespace)


class UpdateSettingRequest(BaseModel):
    """Request body for updating a setting value.

    Attributes:
        value: New value as a string (all types serialised).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    value: str = Field(max_length=65536, description="New value as string")


class TestSinkConfigRequest(BaseModel):
    """Request body for validating a sink configuration.

    Attributes:
        sink_overrides: JSON object of per-sink overrides.
        custom_sinks: JSON array of custom sink definitions.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    sink_overrides: str = Field(
        default="{}",
        max_length=65536,
        description="JSON object of per-sink overrides",
    )
    custom_sinks: str = Field(
        default="[]",
        max_length=65536,
        description="JSON array of custom sink definitions",
    )


class TestSinkConfigResponse(BaseModel):
    """Response body for sink configuration validation.

    Attributes:
        valid: Whether the configuration is valid.
        error: Validation error message (None when valid).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    valid: bool
    error: NotBlankStr | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        """Ensure valid=True implies error is None and vice-versa."""
        if self.valid and self.error is not None:
            msg = "valid=True requires error to be None"
            raise ValueError(msg)
        if not self.valid and self.error is None:
            msg = "valid=False requires a non-None error"
            raise ValueError(msg)
        return self


class SecurityConfigExportResponse(BaseModel):
    """Exported security configuration with metadata."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    config: dict[str, Any]
    exported_at: AwareDatetime
    custom_policies_warning: NotBlankStr | None = None


class SecurityConfigImportRequest(BaseModel):
    """Request body to import a security configuration."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    config: dict[str, Any]


def _sink_to_dict(
    sink: SinkConfig,
    *,
    is_default: bool,
    enabled: bool = True,
    routing_prefixes: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Convert a SinkConfig to a plain dict for API responses.

    Args:
        sink: Sink configuration to convert.
        is_default: Whether this is a default (built-in) sink.
        enabled: Whether the sink is currently active.
        routing_prefixes: Custom routing prefixes (if routing overrides apply).

    Returns:
        Dict representation with all sink fields.
    """
    identifier: str
    if sink.sink_type == SinkType.CONSOLE:
        identifier = CONSOLE_SINK_ID
    else:
        identifier = sink.file_path or ""

    rotation: dict[str, Any] | None = None
    if sink.rotation is not None:
        rotation = {
            "strategy": sink.rotation.strategy.value,
            "max_bytes": sink.rotation.max_bytes,
            "backup_count": sink.rotation.backup_count,
        }

    return {
        "identifier": identifier,
        "sink_type": sink.sink_type.value,
        "level": sink.level.value,
        "json_format": sink.json_format,
        "rotation": rotation,
        "is_default": is_default,
        "enabled": enabled,
        "routing_prefixes": list(routing_prefixes) if routing_prefixes else [],
    }


def _validate_namespace(namespace: str) -> None:
    """Raise 404 if namespace is not a known SettingNamespace member."""
    if namespace not in _VALID_NAMESPACES:
        msg = f"Unknown namespace: {namespace!r}"
        logger.warning(
            SETTINGS_NOT_FOUND,
            namespace=namespace,
            reason="unknown_namespace",
        )
        raise NotFoundError(msg)


async def _check_setting_etag(
    request: Request[Any, Any, Any],
    app_state: AppState,
    namespace: str,
    key: str,
) -> str | None:
    """Validate If-Match header against current setting ETag.

    Args:
        request: Incoming request with optional ``If-Match`` header.
        app_state: Application state for settings lookup.
        namespace: Setting namespace.
        key: Setting key.

    Returns:
        The current ``updated_at`` value when ``If-Match`` is
        present (used for atomic compare-and-swap), or ``None``
        when no ``If-Match`` header is provided.

    Raises:
        NotFoundError: If the setting does not exist (HTTP 404).
        VersionConflictError: If the ETag does not match.
    """
    if_match = request.headers.get("if-match")
    if not if_match:
        return None
    try:
        current = await app_state.settings_service.get_entry(
            namespace,
            key,
        )
    except SettingNotFoundError as exc:
        logger.warning(
            SETTINGS_NOT_FOUND,
            namespace=namespace,
            key=key,
            operation="etag_check",
        )
        raise NotFoundError(str(exc)) from exc
    current_etag = compute_etag(
        current.value,
        current.updated_at or "",
    )
    check_if_match(if_match, current_etag, f"{namespace}:{key}")
    return current.updated_at or ""


class SettingsController(Controller):
    """CRUD for runtime-editable settings with schema introspection."""

    path = "/settings"
    tags = ("settings",)
    guards = [require_read_access]  # noqa: RUF012

    @get("/_schema")
    async def get_full_schema(
        self,
        state: State,
    ) -> ApiResponse[tuple[SettingDefinition, ...]]:
        """Return all setting definitions for UI schema generation.

        Args:
            state: Application state.

        Returns:
            All setting definitions.
        """
        app_state: AppState = state.app_state
        schema = app_state.settings_service.get_schema()
        return ApiResponse(data=schema)

    @get("/_schema/{namespace:str}")
    async def get_namespace_schema(
        self,
        state: State,
        namespace: PathNamespace,
    ) -> ApiResponse[tuple[SettingDefinition, ...]]:
        """Return setting definitions for a specific namespace.

        Args:
            state: Application state.
            namespace: Namespace to filter by.

        Returns:
            Definitions in the namespace.
        """
        _validate_namespace(namespace)
        app_state: AppState = state.app_state
        schema = app_state.settings_service.get_schema(namespace=namespace)
        return ApiResponse(data=schema)

    @get()
    async def list_all_settings(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = 50,
    ) -> PaginatedResponse[SettingEntry]:
        """List settings with resolved values, keyset-paginated.

        Sensitive values are masked. Sorted by ``(namespace, key)``
        with the cursor encoding the last seen ``f"{namespace}:{key}"``;
        the next page reads ``WHERE sort_key > after_key``. Keyset
        contract is stable under concurrent definition / override
        changes -- no duplicates or skips when the registry shifts
        between requests. Pagination is pushed into
        :meth:`SettingsService.get_page`, so the controller only pays
        the resolve cost for the rows it actually returns.

        Args:
            state: Application state.
            cursor: Opaque keyset cursor from a previous page.
            limit: Page size (default 50, max defined by ``MAX_LIMIT``).

        Returns:
            Paginated response of resolved setting entries.

        Raises:
            InvalidCursorError: HTTP 400 -- malformed, tampered, or
                signed by a different secret.
        """
        app_state: AppState = state.app_state
        after_key = (
            decode_keyset_cursor(cursor, secret=app_state.cursor_secret)
            if cursor is not None
            else None
        )
        page, has_more = await app_state.settings_service.get_page(
            after_key=after_key,
            limit=limit,
        )
        next_after_key = (
            f"{page[-1].definition.namespace}:{page[-1].definition.key}"
            if has_more and page
            else None
        )
        meta = encode_keyset_meta(
            next_after_key=next_after_key,
            has_more=has_more,
            limit=limit,
            secret=app_state.cursor_secret,
        )
        return PaginatedResponse(data=page, pagination=meta)

    @get("/{namespace:str}")
    async def get_namespace_settings(
        self,
        state: State,
        namespace: PathNamespace,
    ) -> ApiResponse[tuple[SettingEntry, ...]]:
        """List resolved settings for a namespace.

        Args:
            state: Application state.
            namespace: Namespace to list.

        Returns:
            Resolved setting entries in the namespace.
        """
        _validate_namespace(namespace)
        app_state: AppState = state.app_state
        entries = await app_state.settings_service.get_namespace(namespace)
        return ApiResponse(data=entries)

    @get("/{namespace:str}/{key:str}")
    async def get_setting(
        self,
        state: State,
        namespace: PathNamespace,
        key: PathKey,
    ) -> Response[ApiResponse[SettingEntry]]:
        """Get a single resolved setting with ETag header.

        Args:
            state: Application state.
            namespace: Setting namespace.
            key: Setting key.

        Returns:
            Resolved setting entry with ETag response header.
        """
        _validate_namespace(namespace)
        app_state: AppState = state.app_state
        try:
            entry = await app_state.settings_service.get_entry(namespace, key)
        except SettingNotFoundError as exc:
            logger.warning(
                SETTINGS_NOT_FOUND,
                namespace=namespace,
                key=key,
                operation="read",
            )
            raise NotFoundError(str(exc)) from exc
        etag = compute_etag(
            entry.value,
            entry.updated_at or "",
        )
        return Response(
            content=ApiResponse(data=entry),
            headers={"ETag": etag},
        )

    @put(
        "/{namespace:str}/{key:str}",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("settings.update", key="user"),
        ],
    )
    async def update_setting(
        self,
        request: Request[Any, Any, Any],
        state: State,
        namespace: PathNamespace,
        key: PathKey,
        data: UpdateSettingRequest,
    ) -> Response[ApiResponse[SettingEntry]]:
        """Update a setting value with optimistic concurrency."""
        _validate_namespace(namespace)
        app_state: AppState = state.app_state

        expected_updated_at = await _check_setting_etag(
            request,
            app_state,
            namespace,
            key,
        )

        try:
            entry = await app_state.settings_service.set(
                namespace,
                key,
                data.value,
                expected_updated_at=expected_updated_at,
                import_source=SettingsImportSource.API_BODY,
            )
        except SettingNotFoundError as exc:
            logger.warning(
                SETTINGS_NOT_FOUND,
                namespace=namespace,
                key=key,
                operation="update",
            )
            raise NotFoundError(str(exc)) from exc
        except SettingValidationError as exc:
            # Log the redacted exception detail server-side so operator
            # triage retains the full reason; the 422 body keeps a
            # client-safe generic message so backend exception text
            # never leaks into the API response.
            logger.warning(
                SETTINGS_VALIDATION_FAILED,
                namespace=namespace,
                key=key,
                operation="update",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Invalid setting value"
            raise DomainValidationError(msg) from exc
        except SettingsEncryptionError:
            logger.exception(
                SETTINGS_ENCRYPTION_ERROR,
                namespace=namespace,
                key=key,
            )
            msg = "Internal error processing sensitive setting"
            raise InternalServerException(msg) from None

        new_etag = compute_etag(
            entry.value,
            entry.updated_at or "",
        )
        return Response(
            content=ApiResponse(data=entry),
            headers={"ETag": new_etag},
        )

    @delete(
        "/{namespace:str}/{key:str}",
        guards=[
            require_ceo_or_manager,
            per_op_rate_limit_from_policy("settings.delete", key="user"),
        ],
        status_code=HTTP_204_NO_CONTENT,
    )
    async def delete_setting(
        self,
        state: State,
        namespace: PathNamespace,
        key: PathKey,
    ) -> None:
        """Delete a DB override, reverting to next source in chain.

        Args:
            state: Application state.
            namespace: Setting namespace.
            key: Setting key.
        """
        _validate_namespace(namespace)
        app_state: AppState = state.app_state
        try:
            await app_state.settings_service.delete(namespace, key)
        except SettingNotFoundError as exc:
            logger.warning(
                SETTINGS_NOT_FOUND,
                namespace=namespace,
                key=key,
                operation="delete",
            )
            raise NotFoundError(str(exc)) from exc

    # ── Observability sink endpoints ──────────────────────────────

    @get("/observability/sinks")
    async def list_sinks(
        self,
        state: State,
    ) -> ApiResponse[list[dict[str, Any]]]:
        """Return merged view of all configured log sinks.

        Reads ``sink_overrides``, ``custom_sinks``, ``root_log_level``,
        and ``enable_correlation`` from settings, merges them with
        DEFAULT_SINKS via the sink config builder, and returns a flat
        list of all active sinks.

        Args:
            state: Application state.

        Returns:
            List of sink configuration dicts.
        """
        app_state: AppState = state.app_state
        svc = app_state.settings_service

        overrides_json, custom_json, raw_level, raw_correlation = await asyncio.gather(
            _get_setting_or_default(svc, "sink_overrides", "{}"),
            _get_setting_or_default(svc, "custom_sinks", "[]"),
            _get_setting_or_default(svc, "root_log_level", "debug"),
            _get_setting_or_default(svc, "enable_correlation", "true"),
        )
        root_level = _parse_root_level(raw_level)
        enable_correlation = raw_correlation.lower() == "true"

        try:
            result = build_log_config_from_settings(
                root_level=root_level,
                enable_correlation=enable_correlation,
                sink_overrides_json=overrides_json,
                custom_sinks_json=custom_json,
            )
        except ValueError as exc:
            logger.warning(
                SETTINGS_OBSERVABILITY_VALIDATION_FAILED,
                error=str(exc),
                sink_overrides=overrides_json,
                custom_sinks=custom_json,
            )
            return ApiResponse(data=_defaults_only_sinks())

        sinks = _build_sink_list(result)
        _append_disabled_defaults(sinks)
        return ApiResponse(data=sinks)

    @post(
        "/observability/sinks/_test",
        guards=[require_ceo_or_manager],
        sync_to_thread=False,
    )
    def test_sink_config(
        self,
        data: TestSinkConfigRequest,
    ) -> ApiResponse[TestSinkConfigResponse]:
        """Validate a sink configuration without persisting.

        Runs the sink config builder against the provided overrides
        and custom sinks to check for validation errors.

        Args:
            data: Request body with sink_overrides and custom_sinks.

        Returns:
            Validation result with valid flag and optional error.
        """
        try:
            build_log_config_from_settings(
                root_level=LogLevel.DEBUG,
                enable_correlation=True,
                sink_overrides_json=data.sink_overrides,
                custom_sinks_json=data.custom_sinks,
            )
        except ValueError as exc:
            msg = _sanitize_error(str(exc))
            return ApiResponse(
                data=TestSinkConfigResponse(valid=False, error=msg),
            )
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(SETTINGS_OBSERVABILITY_VALIDATION_FAILED)
            return ApiResponse(
                data=TestSinkConfigResponse(
                    valid=False,
                    error="Internal error validating sink configuration",
                ),
            )
        return ApiResponse(
            data=TestSinkConfigResponse(valid=True),
        )

    # ── Security config export/import ──────────────────────────

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

    @post("/security/import", guards=[require_ceo_or_manager])
    async def import_security_config(
        self,
        state: State,
        data: SecurityConfigImportRequest,
    ) -> ApiResponse[SecurityConfigExportResponse]:
        """Import, validate, and persist a security configuration.

        Validates the payload as a ``SecurityConfig``, then persists
        each field that has a registered setting through the
        :class:`SettingsService`.

        Args:
            state: Application state.
            data: Import request with config dict.

        Returns:
            The validated and persisted config.

        Raises:
            DomainValidationError: If the config fails validation
                (HTTP 422).
        """
        app_state: AppState = state.app_state
        try:
            validated = SecurityConfig.model_validate(data.config)
        except ValidationError as exc:
            # Redact: ``str(exc)`` (and the f-string substitution below)
            # would surface rejected input values from the import payload,
            # which can hold secrets or operator-sensitive configuration.
            # The 422 response keeps a generic message; full diagnostic
            # detail stays on the server-side warning stream via
            # ``safe_error_description``.  SEC-1.
            logger.warning(
                API_SECURITY_CONFIG_IMPORT_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Invalid security config"
            raise DomainValidationError(msg) from exc

        await _persist_security_settings(
            app_state.settings_service,
            validated,
            import_source=SettingsImportSource.API_BODY,
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


# ── Security import helpers ────────────────────────────────────

_SECURITY_SETTING_FIELDS: dict[str, str] = {
    "enabled": "enabled",
    "audit_enabled": "audit_enabled",
    "post_tool_scanning_enabled": "post_tool_scanning_enabled",
    "output_scan_policy_type": "output_scan_policy_type",
}
"""Maps ``SecurityConfig`` field names to setting keys."""


async def _persist_security_settings(
    svc: SettingsService,
    config: SecurityConfig,
    *,
    import_source: SettingsImportSource,
) -> None:
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
        return
    # ``expected_updated_at_map`` of all-empty strings means
    # "first-write" semantics for every key; matches the prior
    # ``svc.set`` loop, which also did not pass CAS versions.
    await svc.set_many(
        items,
        expected_updated_at_map={(ns_val, key): "" for ns_val, key, _ in items},
        import_source=import_source,
    )


# ── Sink helpers (extracted for <50 line methods) ────────────────


async def _get_setting_or_default(
    svc: SettingsService,
    key: str,
    fallback: str,
) -> str:
    """Fetch an observability setting, falling back on not-found.

    Args:
        svc: Settings service instance.
        key: Setting key within the OBSERVABILITY namespace.
        fallback: Default value when the setting is not registered.

    Returns:
        The resolved value string.
    """
    try:
        val = await svc.get(SettingNamespace.OBSERVABILITY, key)
    except SettingNotFoundError:
        logger.debug(
            SETTINGS_NOT_FOUND,
            namespace=SettingNamespace.OBSERVABILITY.value,
            key=key,
        )
        return fallback
    except Exception:
        logger.warning(
            SETTINGS_OBSERVABILITY_VALIDATION_FAILED,
            namespace=SettingNamespace.OBSERVABILITY.value,
            key=key,
            error="Failed to resolve observability setting",
            exc_info=True,
        )
        return fallback
    return val.value


def _parse_root_level(raw: str) -> LogLevel:
    """Convert a stored root_log_level string to a LogLevel enum.

    Args:
        raw: Level string from settings (case-insensitive).

    Returns:
        Matching LogLevel, defaulting to DEBUG on invalid input.
    """
    try:
        return LogLevel(raw.upper())
    except ValueError:
        logger.warning(
            SETTINGS_OBSERVABILITY_VALIDATION_FAILED,
            key="root_log_level",
            error=f"Invalid log level {raw!r}, defaulting to DEBUG",
        )
        return LogLevel.DEBUG


def _build_sink_list(
    result: SinkBuildResult,
) -> list[dict[str, Any]]:
    """Build the active sink list from a SinkBuildResult.

    Args:
        result: SinkBuildResult from the config builder.

    Returns:
        List of sink dicts for all active sinks.
    """
    sinks: list[dict[str, Any]] = []
    for sink in result.config.sinks:
        file_path = sink.file_path
        is_default = (
            sink.sink_type == SinkType.CONSOLE or file_path in DEFAULT_FILE_PATHS
        )
        routing = (
            result.routing_overrides.get(file_path) if file_path is not None else None
        )
        sinks.append(
            _sink_to_dict(
                sink,
                is_default=is_default,
                routing_prefixes=routing,
            )
        )
    return sinks


def _append_disabled_defaults(
    sinks: list[dict[str, Any]],
) -> None:
    """Append disabled default sinks not present in the active list.

    Mutates *sinks* in place, adding entries for any default sink
    that was removed by overrides.

    Args:
        sinks: Mutable list of active sink dicts.
    """
    active_ids = {s["identifier"] for s in sinks}
    for default_sink in DEFAULT_SINKS:
        identifier = (
            CONSOLE_SINK_ID
            if default_sink.sink_type == SinkType.CONSOLE
            else (default_sink.file_path or "")
        )
        if identifier not in active_ids:
            sinks.append(
                _sink_to_dict(
                    default_sink,
                    is_default=True,
                    enabled=False,
                )
            )


def _defaults_only_sinks() -> list[dict[str, Any]]:
    """Return all DEFAULT_SINKS as enabled dicts (fallback path).

    Returns:
        List of sink dicts with all defaults enabled.
    """
    return [_sink_to_dict(sink, is_default=True) for sink in DEFAULT_SINKS]


def _sanitize_error(raw: str) -> str:
    """Strip validation hint suffixes from a sink builder error.

    Truncates error messages at the first valid-key/valid-value
    enumeration suffix to avoid leaking internal config details.

    Args:
        raw: Raw error message from the sink config builder.

    Returns:
        Sanitized error string safe for API responses.
    """
    result = raw.split(". Valid keys:", maxsplit=1)[0].split(". Valid: ", maxsplit=1)[0]
    return result or "Validation failed"
