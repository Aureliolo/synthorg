# module-kind: controller
"""Observability-sink settings endpoints (list + validate)."""

import asyncio

from litestar import Controller, get, post
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api.controllers.settings._sinks import (
    SinkInfoResponse,
    TestSinkConfigRequest,
    TestSinkConfigResponse,
    _append_disabled_defaults,
    _build_sink_list,
    _defaults_only_sinks,
    _get_setting_or_default,
    _parse_root_level,
    _sanitize_error,
)
from synthorg.api.dto import DEFAULT_LIMIT, ApiResponse, PaginatedResponse
from synthorg.api.guards import require_ceo_or_manager, require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import compare_ci
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.enums import LogLevel
from synthorg.observability.events.settings import (
    SETTINGS_OBSERVABILITY_VALIDATION_FAILED,
)
from synthorg.observability.sink_config_builder import build_log_config_from_settings
from synthorg.settings.errors import SinkConfigValidationError
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)


class SettingsObservabilityController(Controller):
    """Read and validate observability log-sink configuration."""

    path = "/settings"
    tags = ("settings",)
    guards = [require_read_access]  # noqa: RUF012

    @get("/observability/sinks")
    async def list_sinks(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = DEFAULT_LIMIT,
    ) -> PaginatedResponse[SinkInfoResponse]:
        """Return merged view of all configured log sinks, paginated.

        Reads ``sink_overrides``, ``custom_sinks``, ``root_log_level``,
        and ``enable_correlation`` from settings, merges them with
        DEFAULT_SINKS via the sink config builder, and returns a flat
        list of all active sinks ordered by ``identifier``.

        Args:
            state: Application state.
            cursor: Opaque cursor from a previous page.
            limit: Page size.

        Returns:
            Paginated typed sink-info responses.
        """
        app_state: AppState = state.app_state
        svc = require_service(
            app_state.slice(SettingsStateSlice).settings_service, "Settings Service"
        )

        overrides_json, custom_json, raw_level, raw_correlation = await asyncio.gather(
            _get_setting_or_default(svc, "sink_overrides", "{}"),
            _get_setting_or_default(svc, "custom_sinks", "[]"),
            _get_setting_or_default(svc, "root_log_level", "debug"),
            _get_setting_or_default(svc, "enable_correlation", "true"),
        )
        root_level = _parse_root_level(raw_level)
        enable_correlation = compare_ci(raw_correlation, "true")

        try:
            result = build_log_config_from_settings(
                root_level=root_level,
                enable_correlation=enable_correlation,
                sink_overrides_json=overrides_json,
                custom_sinks_json=custom_json,
            )
        except ValueError as exc:
            # ``overrides_json`` / ``custom_json`` are operator-supplied
            # blobs that may contain filesystem paths, HTTP / OTLP
            # endpoints with embedded query tokens, or syslog auth
            # material. Logging them verbatim turns a parse failure
            # into a secret-leak vector. Emit only coarse metadata so
            # the sink count + presence are still observable.
            logger.warning(
                SETTINGS_OBSERVABILITY_VALIDATION_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                has_sink_overrides=overrides_json != "{}",
                sink_overrides_length=len(overrides_json),
                has_custom_sinks=custom_json != "[]",
                custom_sinks_length=len(custom_json),
            )
            sinks = _defaults_only_sinks()
        else:
            sinks = _append_disabled_defaults(_build_sink_list(result))

        ordered = tuple(sorted(sinks, key=lambda s: s.identifier))
        page, meta = paginate_cursor(
            ordered,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        return PaginatedResponse[SinkInfoResponse](data=page, pagination=meta)

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

        Raises:
            SinkConfigValidationError: Raised on the corresponding failure path.
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            log_exception_redacted(
                logger, SETTINGS_OBSERVABILITY_VALIDATION_FAILED, exc
            )
            msg = "Internal error validating sink configuration"
            raise SinkConfigValidationError(msg) from None
        return ApiResponse(
            data=TestSinkConfigResponse(valid=True),
        )
