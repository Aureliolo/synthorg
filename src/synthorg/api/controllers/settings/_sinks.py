"""Observability-sink DTOs and helpers for the settings controller.

Pure helper module: the sink-info wire models plus the fingerprinting,
merge, and default-materialisation helpers the observability settings
endpoints use. No Litestar surface; ``settings.observability`` imports these.
"""

import hashlib
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.config import DEFAULT_SINKS, SinkConfig
from synthorg.observability.enums import LogLevel, SinkType
from synthorg.observability.events.settings import (
    SETTINGS_NOT_FOUND,
    SETTINGS_OBSERVABILITY_VALIDATION_FAILED,
)
from synthorg.observability.sink_config_builder import (
    CONSOLE_SINK_ID,
    DEFAULT_FILE_PATHS,
    SinkBuildResult,
)
from synthorg.settings.definitions.api import SINK_IDENTIFIER_FINGERPRINT_LENGTH
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.service import SettingsService

logger = get_logger(__name__)


class TestSinkConfigRequest(BaseModel):
    """Request body for validating a sink configuration.

    Attributes:
        sink_overrides: JSON object of per-sink overrides.
        custom_sinks: JSON array of custom sink definitions.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    valid: bool
    error: NotBlankStr | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        """Ensure valid=True implies error is None and vice-versa.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if self.valid and self.error is not None:
            msg = "valid=True requires error to be None"
            raise ValueError(msg)
        if not self.valid and self.error is None:
            msg = "valid=False requires a non-None error"
            raise ValueError(msg)
        return self


class SinkRotationResponse(BaseModel):
    """Rotation policy summary for a file sink."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    strategy: NotBlankStr
    max_bytes: int = Field(ge=0)
    backup_count: int = Field(ge=0)


class SinkInfoResponse(BaseModel):
    """Single observability-sink summary returned by /settings/observability/sinks.

    Mirrors the frontend ``SinkInfo`` interface field-for-field so the
    pagination wire envelope stays typed end-to-end instead of leaking
    a ``dict[str, JsonValue]``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    identifier: NotBlankStr
    sink_type: NotBlankStr
    level: NotBlankStr
    json_format: bool
    rotation: SinkRotationResponse | None = None
    is_default: bool
    enabled: bool
    routing_prefixes: tuple[str, ...] = ()


def _hash_sink_target(target: str) -> str:
    """Return a stable, non-reversible fingerprint for a sink destination.

    Used by :func:`_sink_identifier` to derive a per-instance API
    identifier without exposing the raw destination string (which can
    embed credentials, query tokens, or auth-bearing path segments
    for HTTP / OTLP sinks, and operator filesystem layout for FILE
    sinks).  The fingerprint length is centralised in
    :data:`SINK_IDENTIFIER_FINGERPRINT_LENGTH` so the wire-format
    contract changes in one place.

    Returns:
        Resulting string.
    """
    return hashlib.sha256(target.encode("utf-8")).hexdigest()[
        :SINK_IDENTIFIER_FINGERPRINT_LENGTH
    ]


def _sink_identifier(sink: SinkConfig) -> str:
    """Return the stable API identifier for a ``SinkConfig``.

    Console sinks use the fixed ``CONSOLE_SINK_ID`` token. Every other
    sink derives a type-prefixed SHA-256 fingerprint over its target:

    * ``FILE``:   ``file:<sha256(file_path)[:16]>``
    * ``SYSLOG``: ``syslog:<sha256("host:port")[:16]>``
    * ``HTTP``:   ``http:<sha256(url)[:16]>``
    * ``OTLP``:   ``otlp:<sha256(endpoint)[:16]>``

    The hash both prevents the public envelope from leaking embedded
    credentials / query tokens and gives identical destinations the
    same identifier, so the active-set membership test in
    :func:`_append_disabled_defaults` and the cursor sort key in the
    listing endpoint stay collision-free on operator-supplied input.

    A sink without any of those endpoint fields is invalid per
    :meth:`SinkConfig._validate_sink_type_fields`; the
    ``unnamed-<type>`` fallback only fires defensively against a
    future sink type that hasn't been wired here yet, never on
    well-formed config.

    Returns:
        Resulting string.
    """
    if sink.sink_type == SinkType.CONSOLE:
        return CONSOLE_SINK_ID
    if sink.file_path:
        return f"file:{_hash_sink_target(sink.file_path)}"
    if sink.sink_type == SinkType.SYSLOG and sink.syslog_host:
        target = f"{sink.syslog_host}:{sink.syslog_port}"
        return f"syslog:{_hash_sink_target(target)}"
    if sink.sink_type == SinkType.HTTP and sink.http_url:
        return f"http:{_hash_sink_target(sink.http_url)}"
    if sink.sink_type == SinkType.OTLP and sink.otlp_endpoint:
        return f"otlp:{_hash_sink_target(sink.otlp_endpoint)}"
    return f"unnamed-{sink.sink_type.value}"


def _sink_to_response(
    sink: SinkConfig,
    *,
    is_default: bool,
    enabled: bool = True,
    routing_prefixes: tuple[str, ...] | None = None,
) -> SinkInfoResponse:
    """Convert a SinkConfig to the typed API response model.

    Returns:
        ``SinkInfoResponse`` instance.
    """
    identifier = _sink_identifier(sink)
    return SinkInfoResponse(
        identifier=NotBlankStr(identifier),
        sink_type=NotBlankStr(sink.sink_type.value),
        level=NotBlankStr(sink.level.value),
        json_format=sink.json_format,
        rotation=SinkRotationResponse(
            strategy=NotBlankStr(sink.rotation.strategy.value),
            max_bytes=sink.rotation.max_bytes,
            backup_count=sink.rotation.backup_count,
        )
        if sink.rotation is not None
        else None,
        is_default=is_default,
        enabled=enabled,
        routing_prefixes=routing_prefixes or (),
    )


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
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            SETTINGS_OBSERVABILITY_VALIDATION_FAILED,
            namespace=SettingNamespace.OBSERVABILITY.value,
            key=key,
            note="Failed to resolve observability setting",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
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
        # Operators may store arbitrary text in this key from a
        # mis-typed CLI invocation; ``raw`` would otherwise leak that
        # into the log sink. Static message keeps the diagnostic
        # generic; the namespace + key are enough for triage.
        logger.warning(
            SETTINGS_OBSERVABILITY_VALIDATION_FAILED,
            key="root_log_level",
            note="Invalid log level value, defaulting to DEBUG",
        )
        return LogLevel.DEBUG


def _build_sink_list(
    result: SinkBuildResult,
) -> list[SinkInfoResponse]:
    """Build the active sink list from a SinkBuildResult.

    Args:
        result: SinkBuildResult from the config builder.

    Returns:
        Typed sink-info responses for all active sinks.
    """
    sinks: list[SinkInfoResponse] = []
    for sink in result.config.sinks:
        file_path = sink.file_path
        is_default = (
            sink.sink_type == SinkType.CONSOLE or file_path in DEFAULT_FILE_PATHS
        )
        routing = (
            result.routing_overrides.get(file_path) if file_path is not None else None
        )
        sinks.append(
            _sink_to_response(
                sink,
                is_default=is_default,
                routing_prefixes=routing,
            )
        )
    return sinks


def _append_disabled_defaults(
    sinks: list[SinkInfoResponse],
) -> list[SinkInfoResponse]:
    """Return ``sinks`` extended with disabled-default entries.

    The active list is left untouched; the returned value carries the
    original entries followed by any default sink that was removed by
    overrides, materialised as ``enabled=False`` responses. Identifier
    matching uses :func:`_sink_identifier` so the active-set membership
    test stays in lockstep with how default sinks are rendered on the
    wire.

    Args:
        sinks: Active sink responses (read-only -- never mutated).

    Returns:
        A new list with the active entries plus any synthesised
        disabled-default entries.
    """
    active_ids = {s.identifier for s in sinks}
    disabled_defaults = [
        _sink_to_response(default_sink, is_default=True, enabled=False)
        for default_sink in DEFAULT_SINKS
        if _sink_identifier(default_sink) not in active_ids
    ]
    return [*sinks, *disabled_defaults]


def _defaults_only_sinks() -> list[SinkInfoResponse]:
    """Return all DEFAULT_SINKS as enabled responses (fallback path).

    Returns:
        Typed sink-info responses with all defaults enabled.
    """
    return [_sink_to_response(sink, is_default=True) for sink in DEFAULT_SINKS]


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
