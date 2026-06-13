# module-kind: complex_service
"""Config resolver -- typed config access backed by SettingsService.

Bridges the gap between :class:`SettingsService` (which returns
:class:`~synthorg.settings.models.SettingValue` objects with a string
``.value``) and consumers that need typed Python objects.  Provides
scalar accessors and composed-read methods that assemble full Pydantic
config models from individually resolved settings.

The size is driven by the breadth of typed accessors the rest of the
codebase consumes: every scalar type (str / int / float / bool / enum
/ json), every composed Pydantic config model (budget / api /
coordination), and every per-namespace bridge config block lands here
so the registry's DB > env > default precedence (and the
``settings.value.resolved`` audit log) fire from one place. Shrinking
requires either generating the bridge-config wrappers or letting each
namespace bridge resolve its own settings, both of which trade the
single audit chokepoint for fragmented resolution paths.
"""

import asyncio
import json
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.observability import get_logger
from synthorg.observability.events.settings import (
    SETTINGS_FETCH_FAILED,
    SETTINGS_NOT_FOUND,
    SETTINGS_VALIDATION_FAILED,
)
from synthorg.observability.redaction import safe_error_description
from synthorg.settings._resolver_batch_reads import resolve_bridge_fields
from synthorg.settings._resolver_coercions import (
    _build_budget_alerts,
    _coerce_batch_size,
    _coerce_vram_gb,
    _parse_bool,
)
from synthorg.settings.bridge_configs import (
    A2ABridgeConfig,
    ApiBridgeConfig,
    ClientBridgeConfig,
    CommunicationBridgeConfig,
    CoordinationBridgeConfig,
    EngineBridgeConfig,
    IntegrationsBridgeConfig,
    MemoryBridgeConfig,
    MetaBridgeConfig,
    NotificationsBridgeConfig,
    ObservabilityBridgeConfig,
    SettingsDispatcherBridgeConfig,
    ToolsBridgeConfig,
    WorkersBridgeConfig,
)
from synthorg.settings.errors import SettingNotFoundError, SettingsEncryptionError
from synthorg.settings.service_protocol import SettingsServiceProtocol

if TYPE_CHECKING:
    # The composed-config read methods (``get_budget_config`` /
    # ``get_api_config`` / ``get_agents`` / ``get_departments`` /
    # ``get_provider_configs`` / ``get_coordination_config``) return concrete
    # config models that are deliberately excluded from
    # ``ConfigResolverProtocol``: consumers hold the resolver by that
    # Protocol's scalar + bridge surface, never these getters. Importing
    # ``api.config`` at module load re-enters ``settings.resolver`` through
    # the api rate-limits -> settings.definitions -> security -> engine ->
    # communication -> notifications.dispatcher cold cycle; ``config.schema``
    # aggregates ``api.config``; and ``engine.coordination.config`` triggers
    # the engine package init on the same loop. This cohesive composed-read
    # surface is therefore named for signatures only, and the value-using
    # getters import their model in the method body.
    from synthorg.api.config import ApiConfig
    from synthorg.budget.config import BudgetConfig
    from synthorg.config.agent_schema import AgentConfig
    from synthorg.config.provider_schema import ProviderConfig
    from synthorg.config.schema import RootConfig
    from synthorg.core.company_departments import Department
    from synthorg.engine.coordination.config import CoordinationConfig

logger = get_logger(__name__)


class ConfigResolver:
    """Typed config accessor backed by :class:`SettingsService`.

    Scalar accessors call ``SettingsService.get()`` and coerce the
    string result to the requested Python type.

    Composed-read methods assemble full Pydantic config models by
    reading individual settings and merging them onto a base config
    loaded from YAML (for fields not yet in the settings registry).

    The ``config`` snapshot is captured at construction time and is
    **not** updated if the underlying ``RootConfig`` is replaced.
    ``ConfigResolver`` and ``AppState`` must always hold the same
    reference; see ``AppState.__init__`` for the wiring invariant.

    Args:
        settings_service: The settings service for value resolution.
        config: Root company configuration used as the base for
            composed reads (provides defaults for unregistered fields).

    Raises:
        TypeError: If *settings_service* is ``None``.
    """

    def __init__(
        self,
        *,
        settings_service: SettingsServiceProtocol,
        config: RootConfig,
    ) -> None:
        # runtime defence for callers without type checking
        if settings_service is None:
            msg = "settings_service must not be None"  # type: ignore[unreachable]
            logger.error(SETTINGS_VALIDATION_FAILED, reason=msg)
            raise TypeError(msg)
        self._settings = settings_service
        self._config = config

    async def get_str(self, namespace: str, key: str) -> str:
        """Resolve a setting as a string.

        Args:
            namespace: Setting namespace.
            key: Setting key.

        Returns:
            The resolved value as a ``str``.

        Raises:
            SettingNotFoundError: If the key is not in the registry.
        """
        try:
            result = await self._settings.get(namespace, key)
        except SettingNotFoundError:
            logger.warning(
                SETTINGS_NOT_FOUND,
                namespace=namespace,
                key=key,
            )
            raise
        return result.value

    async def get_int(self, namespace: str, key: str) -> int:
        """Resolve a setting as an integer.

        Args:
            namespace: Setting namespace.
            key: Setting key.

        Returns:
            The resolved value as an ``int``.

        Raises:
            SettingNotFoundError: If the key is not in the registry.
            ValueError: If the value cannot be parsed as an integer.
        """
        try:
            result = await self._settings.get(namespace, key)
        except SettingNotFoundError:
            logger.warning(
                SETTINGS_NOT_FOUND,
                namespace=namespace,
                key=key,
            )
            raise
        try:
            return int(result.value)
        except ValueError:
            logger.warning(
                SETTINGS_VALIDATION_FAILED,
                namespace=namespace,
                key=key,
                reason="invalid_integer",
            )
            msg = f"Setting {namespace}/{key} has an invalid integer value"
            raise ValueError(msg) from None

    async def get_float(self, namespace: str, key: str) -> float:
        """Resolve a setting as a float.

        Args:
            namespace: Setting namespace.
            key: Setting key.

        Returns:
            The resolved value as a ``float``.

        Raises:
            SettingNotFoundError: If the key is not in the registry.
            ValueError: If the value cannot be parsed as a float.
        """
        try:
            result = await self._settings.get(namespace, key)
        except SettingNotFoundError:
            logger.warning(
                SETTINGS_NOT_FOUND,
                namespace=namespace,
                key=key,
            )
            raise
        try:
            return float(result.value)
        except ValueError:
            logger.warning(
                SETTINGS_VALIDATION_FAILED,
                namespace=namespace,
                key=key,
                reason="invalid_float",
            )
            msg = f"Setting {namespace}/{key} has an invalid float value"
            raise ValueError(msg) from None

    async def get_bool(self, namespace: str, key: str) -> bool:
        """Resolve a setting as a boolean.

        Accepted values are delegated to :func:`_parse_bool`.

        Args:
            namespace: Setting namespace.
            key: Setting key.

        Returns:
            The resolved value as a ``bool``.

        Raises:
            SettingNotFoundError: If the key is not in the registry.
            ValueError: If the value is not a recognized boolean string.
        """
        try:
            result = await self._settings.get(namespace, key)
        except SettingNotFoundError:
            logger.warning(
                SETTINGS_NOT_FOUND,
                namespace=namespace,
                key=key,
            )
            raise
        try:
            return _parse_bool(result.value)
        except ValueError:
            logger.warning(
                SETTINGS_VALIDATION_FAILED,
                namespace=namespace,
                key=key,
                reason="invalid_boolean",
            )
            msg = f"Setting {namespace}/{key} is not a recognized boolean"
            raise ValueError(msg) from None

    async def get_enum[E: StrEnum](
        self,
        namespace: str,
        key: str,
        enum_cls: type[E],
    ) -> E:
        """Resolve a setting as a ``StrEnum`` member.

        Args:
            namespace: Setting namespace.
            key: Setting key.
            enum_cls: The enum class to coerce the value into.

        Returns:
            The matching enum member.

        Raises:
            SettingNotFoundError: If the key is not in the registry.
            ValueError: If the value does not match any enum member.
        """
        try:
            result = await self._settings.get(namespace, key)
        except SettingNotFoundError:
            logger.warning(
                SETTINGS_NOT_FOUND,
                namespace=namespace,
                key=key,
            )
            raise
        try:
            return enum_cls(result.value)
        except ValueError:
            logger.warning(
                SETTINGS_VALIDATION_FAILED,
                namespace=namespace,
                key=key,
                reason="invalid_enum",
                enum_cls=enum_cls.__name__,
            )
            msg = f"Setting {namespace}/{key} has an invalid {enum_cls.__name__} value"
            raise ValueError(msg) from None

    async def get_autonomy_level(self) -> AutonomyLevel:
        """Resolve the company-wide default autonomy level.

        Returns:
            The resolved ``AutonomyLevel`` enum member.

        Raises:
            SettingNotFoundError: If the autonomy_level key is
                not registered.
            ValueError: If the stored value does not match any
                ``AutonomyLevel`` member.
        """
        from synthorg.core.autonomy_enums import AutonomyLevel  # noqa: PLC0415

        return await self.get_enum("company", "autonomy_level", AutonomyLevel)

    async def get_json(  # type: ignore[explicit-any]  # parsed JSON feeds pydantic validation
        self, namespace: str, key: str
    ) -> Any:
        """Resolve a setting as parsed JSON.

        ``Any`` is deliberate: callers hand the parsed value straight to
        a Pydantic constructor or shape-check it themselves, so a
        narrower static type would only force casts at every call site.

        Args:
            namespace: Setting namespace.
            key: Setting key.

        Returns:
            The parsed JSON value (list, dict, scalar, etc.).
            Note that JSON ``null`` parses to Python ``None``.

        Raises:
            SettingNotFoundError: If the key is not in the registry.
            SettingsEncryptionError: If the value cannot be decrypted.
            ValueError: If the value is not valid JSON.
        """
        try:
            result = await self._settings.get(namespace, key)
        except SettingNotFoundError:
            logger.warning(
                SETTINGS_NOT_FOUND,
                namespace=namespace,
                key=key,
            )
            raise
        except SettingsEncryptionError:
            logger.warning(
                SETTINGS_FETCH_FAILED,
                namespace=namespace,
                key=key,
                reason="decryption_failed",
            )
            raise
        try:
            return json.loads(result.value)
        except json.JSONDecodeError as exc:
            logger.warning(
                SETTINGS_VALIDATION_FAILED,
                namespace=namespace,
                key=key,
                reason="invalid_json",
            )
            msg = f"Setting {namespace}/{key} has an invalid JSON value"
            raise ValueError(msg) from exc

    async def _resolve_list_setting[ModelT: BaseModel](
        self,
        namespace: str,
        key: str,
        model_cls: type[ModelT],
        fallback: tuple[ModelT, ...],
    ) -> tuple[ModelT, ...]:
        """Resolve a JSON list setting to a tuple of validated models.

        Falls back to *fallback* on ``None``, invalid JSON, wrong
        shape, or schema validation failure.

        Returns:
            A tuple of validated model instances parsed from the JSON
            list, or *fallback* on any parse or schema-validation
            failure.
        """
        from pydantic import ValidationError  # noqa: PLC0415

        try:
            raw = await self.get_json(namespace, key)
        except ValueError:
            logger.warning(
                SETTINGS_FETCH_FAILED,
                namespace=namespace,
                key=key,
                reason="invalid_json_fallback",
            )
            return fallback
        if raw is None:
            return fallback
        if not isinstance(raw, list):
            logger.warning(
                SETTINGS_FETCH_FAILED,
                namespace=namespace,
                key=key,
                reason="expected_list_fallback",
                value_type=type(raw).__name__,
            )
            return fallback
        try:
            return tuple(model_cls.model_validate(item) for item in raw)
        except ValidationError:
            logger.warning(
                SETTINGS_FETCH_FAILED,
                namespace=namespace,
                key=key,
                reason="invalid_schema_fallback",
            )
            return fallback

    async def _resolve_dict_setting[ModelT: BaseModel](
        self,
        namespace: str,
        key: str,
        model_cls: type[ModelT],
        fallback: dict[str, ModelT],
    ) -> dict[str, ModelT]:
        """Resolve a JSON dict setting to a dict of validated models.

        Falls back to *fallback* on ``None``, invalid JSON, wrong
        shape, or schema validation failure.

        Returns:
            A dict mapping names to validated model instances parsed
            from the JSON dict, or *fallback* on any parse or
            schema-validation failure.
        """
        from pydantic import ValidationError  # noqa: PLC0415

        try:
            raw = await self.get_json(namespace, key)
        except ValueError:
            logger.warning(
                SETTINGS_FETCH_FAILED,
                namespace=namespace,
                key=key,
                reason="invalid_json_fallback",
            )
            return fallback
        if raw is None:
            return fallback
        if not isinstance(raw, dict):
            logger.warning(
                SETTINGS_FETCH_FAILED,
                namespace=namespace,
                key=key,
                reason="expected_dict_fallback",
                value_type=type(raw).__name__,
            )
            return fallback
        try:
            return {name: model_cls.model_validate(conf) for name, conf in raw.items()}
        except ValidationError:
            logger.warning(
                SETTINGS_FETCH_FAILED,
                namespace=namespace,
                key=key,
                reason="invalid_schema_fallback",
            )
            return fallback

    async def get_agents(self) -> tuple[AgentConfig, ...]:
        """Resolve agent configurations from settings.

        Falls back to ``RootConfig.agents`` if the setting value is
        ``None``, contains invalid JSON, or fails schema validation.
        An explicit empty list ``[]`` is a valid override.

        Returns:
            A tuple of ``AgentConfig`` instances, falling back to
            ``RootConfig.agents`` on parse or validation failure.

        Raises:
            SettingNotFoundError: If the agents key is not
                in the registry.
            SettingsEncryptionError: If decryption fails.
        """
        from synthorg.config.agent_schema import AgentConfig  # noqa: PLC0415

        return await self._resolve_list_setting(
            "company",
            "agents",
            AgentConfig,
            self._config.agents,
        )

    async def get_departments(self) -> tuple[Department, ...]:
        """Resolve department configurations from settings.

        Falls back to ``RootConfig.departments`` if the setting value
        is ``None``, contains invalid JSON, or fails schema validation.
        An explicit empty list ``[]`` is a valid override.

        Returns:
            A tuple of ``Department`` instances, falling back to
            ``RootConfig.departments`` on parse or validation failure.

        Raises:
            SettingNotFoundError: If the departments key is not
                in the registry.
            SettingsEncryptionError: If decryption fails.
        """
        from synthorg.core.company_departments import Department  # noqa: PLC0415

        return await self._resolve_list_setting(
            "company",
            "departments",
            Department,
            self._config.departments,
        )

    async def get_provider_configs(self) -> Mapping[str, ProviderConfig]:
        """Resolve provider configurations from settings.

        Falls back to ``RootConfig.providers`` if the setting value
        is ``None``, contains invalid JSON, or fails schema validation.
        An explicit empty dict ``{}`` is a valid override.

        The returned mapping is wrapped in :class:`types.MappingProxyType`
        to prevent callers from mutating the resolver's view of provider
        state.  A deep copy is unnecessary because ``ProviderConfig`` is
        a fully-immutable frozen Pydantic model whose container fields
        are ``tuple[...]`` (also immutable) and whose nested configs
        are likewise frozen, so the exposed values cannot be mutated
        through the returned mapping.  Build a fresh ``dict`` via
        comprehension or unpacking (e.g.  ``{**providers, name:
        config}``) if a mutable copy is needed.

        Returns:
            An immutable ``MappingProxyType`` mapping provider names to
            ``ProviderConfig`` instances, falling back to
            ``RootConfig.providers`` on parse or validation failure.

        Raises:
            SettingNotFoundError: If the ``configs`` key is not
                in the registry.
            SettingsEncryptionError: If decryption fails.
        """
        from synthorg.config.provider_schema import ProviderConfig  # noqa: PLC0415

        configs = await self._resolve_dict_setting(
            "providers",
            "configs",
            ProviderConfig,
            dict(self._config.providers),
        )
        return MappingProxyType(configs)

    async def get_budget_config(self) -> BudgetConfig:
        """Assemble a ``BudgetConfig`` from individually resolved settings.

        Starts from the YAML-loaded base config and overrides fields
        that have registered settings definitions.  Unregistered fields
        on nested models (e.g. ``auto_downgrade.downgrade_map``,
        ``auto_downgrade.boundary``) keep their YAML values.

        Uses ``asyncio.TaskGroup`` to resolve all settings in parallel.
        If any individual resolution fails, the ``ExceptionGroup`` is
        unwrapped and the first cause is re-raised directly.

        Returns:
            A ``BudgetConfig`` with DB/env overrides applied.

        Raises:
            SettingNotFoundError: If a required budget setting is
                missing from the registry.
            ValueError: If a resolved value cannot be parsed or if
                the assembled alert thresholds violate the ordering
                constraint (``warn_at < critical_at < hard_stop_at``).
        """
        base = self._config.budget

        try:
            async with asyncio.TaskGroup() as tg:
                t_monthly = tg.create_task(self.get_float("budget", "total_monthly"))
                t_per_task = tg.create_task(self.get_float("budget", "per_task_limit"))
                t_daily = tg.create_task(
                    self.get_float("budget", "per_agent_daily_limit")
                )
                t_downgrade_en = tg.create_task(
                    self.get_bool("budget", "auto_downgrade_enabled")
                )
                t_downgrade_th = tg.create_task(
                    self.get_int("budget", "auto_downgrade_threshold")
                )
                t_reset = tg.create_task(self.get_int("budget", "reset_day"))
                t_warn = tg.create_task(self.get_int("budget", "alert_warn_at"))
                t_crit = tg.create_task(self.get_int("budget", "alert_critical_at"))
                t_stop = tg.create_task(self.get_int("budget", "alert_hard_stop_at"))
                t_currency = tg.create_task(self.get_str("budget", "currency"))
        except ExceptionGroup as eg:
            first_failure = eg.exceptions[0]
            logger.warning(
                SETTINGS_FETCH_FAILED,
                namespace="budget",
                key="_composed",
                error_count=len(eg.exceptions),
                error_type=type(first_failure).__name__,
                error=safe_error_description(first_failure),
            )
            raise first_failure from eg

        alerts = _build_budget_alerts(t_warn.result(), t_crit.result(), t_stop.result())
        return base.model_copy(
            update={
                "total_monthly": t_monthly.result(),
                "per_task_limit": t_per_task.result(),
                "per_agent_daily_limit": t_daily.result(),
                "reset_day": t_reset.result(),
                "currency": t_currency.result(),
                "alerts": alerts,
                "auto_downgrade": base.auto_downgrade.model_copy(
                    update={
                        "enabled": t_downgrade_en.result(),
                        "threshold": t_downgrade_th.result(),
                    },
                ),
            },
        )

    async def get_api_config(self) -> ApiConfig:
        """Assemble an ``ApiConfig`` with runtime-editable overrides.

        Resolves the five runtime-relevant API settings (rate-limit
        max requests for both tiers, rate-limit time unit, JWT expiry,
        min password length) and merges them onto the YAML-loaded base
        config.

        Bootstrap-only settings (``server_host``, ``server_port``,
        ``api_prefix``, ``ssl_certfile``, ``ssl_keyfile``,
        ``ssl_ca_certs``, ``trusted_proxies``,
        ``cors_allowed_origins``,
        ``rate_limit_exclude_paths``, ``auth_exclude_paths``) are
        **not** resolved -- they are baked into the Litestar app at
        construction and require a restart to take effect.  The two
        rate-limit max-request settings are also bootstrap-only
        (``restart_required=True``) but are resolved here so the
        assembled ``ApiConfig`` reflects DB/env overrides at startup.

        Uses ``asyncio.TaskGroup`` to resolve all settings in parallel.

        Returns:
            An ``ApiConfig`` with DB/env overrides applied to the
            runtime-editable fields.

        Raises:
            SettingNotFoundError: If a required API setting is
                missing from the registry.
            ValueError: If a resolved value cannot be parsed.
        """
        from synthorg.api.config import RateLimitTimeUnit  # noqa: PLC0415

        base = self._config.api

        try:
            async with asyncio.TaskGroup() as tg:
                t_unauth = tg.create_task(
                    self.get_int("api", "rate_limit_unauth_max_requests")
                )
                t_auth = tg.create_task(
                    self.get_int("api", "rate_limit_auth_max_requests")
                )
                t_time_unit = tg.create_task(
                    self.get_enum("api", "rate_limit_time_unit", RateLimitTimeUnit)
                )
                t_jwt_exp = tg.create_task(self.get_int("api", "jwt_expiry_minutes"))
                t_min_pw = tg.create_task(self.get_int("api", "min_password_length"))
        except ExceptionGroup as eg:
            first_failure = eg.exceptions[0]
            logger.warning(
                SETTINGS_FETCH_FAILED,
                namespace="api",
                key="_composed",
                error_count=len(eg.exceptions),
                error_type=type(first_failure).__name__,
                error=safe_error_description(first_failure),
            )
            raise first_failure from eg

        return base.model_copy(
            update={
                "rate_limit": base.rate_limit.model_copy(
                    update={
                        "unauth_max_requests": t_unauth.result(),
                        "auth_max_requests": t_auth.result(),
                        "time_unit": t_time_unit.result(),
                    },
                ),
                "auth": base.auth.model_copy(
                    update={
                        "jwt_expiry_minutes": t_jwt_exp.result(),
                        "min_password_length": t_min_pw.result(),
                    },
                ),
            },
        )

    async def get_coordination_config(
        self,
        *,
        max_concurrency_per_wave: int | None = None,
        fail_fast: bool | None = None,
    ) -> CoordinationConfig:
        """Assemble a per-run ``CoordinationConfig`` from settings.

        Resolves coordination settings from the settings service using
        ``asyncio.TaskGroup`` for parallel resolution, then applies
        request-level overrides on top.  If any individual resolution
        fails, the ``ExceptionGroup`` is unwrapped and the first cause
        is re-raised directly.

        ``CoordinationConfig`` is constructed from scratch (not via
        ``model_copy``) because all its fields are registered in the
        settings registry.  The ``default_topology`` setting is
        consumed by :class:`MultiAgentCoordinator` via the
        ``default_topology_provider`` kwarg wired in
        ``engine/coordination/factory.py`` -- the provider is a
        callable that reads ``config.topology`` at call time so
        runtime setting changes propagate without a coordinator
        rebuild. ``default_topology`` is not part of
        ``CoordinationConfig`` because that model is reconstructed
        per request rather than rebuilt at settings-change time.

        Args:
            max_concurrency_per_wave: Request-level override for max
                concurrency (takes precedence over the setting value).
            fail_fast: Request-level override for fail-fast behaviour.

        Returns:
            A ``CoordinationConfig`` with settings + request overrides.

        Raises:
            SettingNotFoundError: If a required coordination setting
                is missing from the registry.
            ValueError: If a resolved value cannot be parsed.
        """
        from synthorg.engine.coordination.config import (  # noqa: PLC0415
            CoordinationConfig,
        )

        try:
            async with asyncio.TaskGroup() as tg:
                t_wave = tg.create_task(
                    self.get_int("coordination", "max_concurrency_per_wave")
                )
                t_ff = tg.create_task(self.get_bool("coordination", "fail_fast"))
                t_iso = tg.create_task(
                    self.get_bool("coordination", "enable_workspace_isolation")
                )
                t_branch = tg.create_task(self.get_str("coordination", "base_branch"))
                t_stall = tg.create_task(
                    self.get_int("coordination", "max_stall_count")
                )
                t_reset = tg.create_task(
                    self.get_int("coordination", "max_reset_count")
                )
        except ExceptionGroup as eg:
            first_failure = eg.exceptions[0]
            logger.warning(
                SETTINGS_FETCH_FAILED,
                namespace="coordination",
                key="_composed",
                error_count=len(eg.exceptions),
                error_type=type(first_failure).__name__,
                error=safe_error_description(first_failure),
            )
            raise first_failure from eg

        return CoordinationConfig(
            max_concurrency_per_wave=(
                max_concurrency_per_wave
                if max_concurrency_per_wave is not None
                else t_wave.result()
            ),
            fail_fast=(fail_fast if fail_fast is not None else t_ff.result()),
            enable_workspace_isolation=t_iso.result(),
            base_branch=t_branch.result(),
            max_stall_count=t_stall.result(),
            max_reset_count=t_reset.result(),
        )

    # ── Config-bridge composed reads (delegation + event-stream) ────

    async def _resolve_bridge_fields(  # type: ignore[explicit-any]  # values feed Model(**values)
        self,
        namespace: str,
        specs: tuple[tuple[str, str], ...],
    ) -> dict[str, Any]:
        """Resolve a bundle of same-namespace settings in parallel.

        Thin delegator: the ``TaskGroup`` fan-out and failed-key
        pinpointing live in
        :func:`synthorg.settings._resolver_batch_reads.resolve_bridge_fields`.

        Args:
            namespace: Setting namespace (e.g. ``"a2a"``).
            specs: Tuple of ``(key, kind)`` pairs to resolve, where
                ``kind`` is one of ``"int"``, ``"float"``, ``"str"``,
                or ``"json"``.

        Returns:
            Dict of ``{key: parsed_value}`` for each spec.

        Raises:
            SettingNotFoundError: If a key is not in the registry.
            ValueError: If a resolved value cannot be parsed.
        """
        return await resolve_bridge_fields(self, namespace, specs)

    async def get_api_bridge_config(self) -> ApiBridgeConfig:
        """Assemble ``ApiBridgeConfig`` from bridged API settings.

        Returns:
            An ``ApiBridgeConfig`` populated with the bridged API
            settings resolved from the settings service.
        """
        from synthorg.settings.bridge_configs import ApiBridgeConfig  # noqa: PLC0415

        values = await self._resolve_bridge_fields(
            "api",
            (
                ("ticket_cleanup_interval_seconds", "float"),
                ("ws_ticket_max_pending_per_user", "int"),
                ("ws_auth_timeout_seconds", "float"),
                ("ws_frame_timeout_seconds", "int"),
                ("auth_revalidate_window_seconds", "int"),
                ("auth_revalidate_max_failures", "int"),
                ("sse_keepalive_seconds", "float"),
                ("max_rpm_default", "int"),
                ("compression_minimum_size_bytes", "int"),
                ("request_max_body_size_bytes", "int"),
                ("max_lifecycle_events_per_query", "int"),
                ("max_audit_records_per_query", "int"),
                ("max_metrics_per_query", "int"),
                ("max_meeting_context_keys", "int"),
                ("rate_limit_gc_every_n_acquires", "int"),
                ("rate_limit_gc_min_horizon_seconds", "int"),
                ("rate_limit_inflight_gc_every_n_acquires", "int"),
                ("rate_limit_inflight_min_retry_after_seconds", "int"),
                ("lifecycle_task_engine_shutdown_seconds", "float"),
                ("lifecycle_meeting_scheduler_shutdown_seconds", "float"),
                ("lifecycle_performance_tracker_shutdown_seconds", "float"),
                ("lifecycle_backup_shutdown_seconds", "float"),
                ("lifecycle_settings_dispatcher_shutdown_seconds", "float"),
                ("lifecycle_bridge_shutdown_seconds", "float"),
                ("lifecycle_distributed_queue_shutdown_seconds", "float"),
                ("lifecycle_message_bus_shutdown_seconds", "float"),
                ("lifecycle_persistence_shutdown_seconds", "float"),
                ("lifecycle_approval_timeout_shutdown_seconds", "float"),
                ("lifecycle_drain_timeout_seconds", "float"),
                ("approval_urgency_critical_seconds", "float"),
                ("approval_urgency_high_seconds", "float"),
                ("csp_docs_external_origins", "json"),
                ("error_docs_base_url", "str"),
            ),
        )
        return ApiBridgeConfig(**values)

    async def get_coordination_bridge_config(self) -> CoordinationBridgeConfig:
        """Assemble ``CoordinationBridgeConfig`` from bridged coordination settings.

        Returns:
            A ``CoordinationBridgeConfig`` populated with the bridged
            coordination settings resolved from the settings service.
        """
        from synthorg.settings.bridge_configs import (  # noqa: PLC0415
            CoordinationBridgeConfig,
        )

        values = await self._resolve_bridge_fields(
            "coordination",
            (("cas_max_attempts", "int"),),
        )
        return CoordinationBridgeConfig(**values)

    async def get_workers_bridge_config(self) -> WorkersBridgeConfig:
        """Assemble ``WorkersBridgeConfig`` from bridged workers settings.

        Returns:
            A ``WorkersBridgeConfig`` populated with the bridged workers
            settings resolved from the settings service.
        """
        from synthorg.settings.bridge_configs import (  # noqa: PLC0415
            WorkersBridgeConfig,
        )

        values = await self._resolve_bridge_fields(
            "workers",
            (
                ("dispatcher_publish_max_attempts", "int"),
                ("dispatcher_publish_backoff_base_seconds", "float"),
                ("dispatcher_publish_backoff_cap_seconds", "float"),
            ),
        )
        return WorkersBridgeConfig(**values)

    async def get_communication_bridge_config(self) -> CommunicationBridgeConfig:
        """Assemble ``CommunicationBridgeConfig`` from bridged settings.

        Returns:
            A ``CommunicationBridgeConfig`` populated with the bridged
            communication settings resolved from the settings service.
        """
        from synthorg.settings.bridge_configs import (  # noqa: PLC0415
            CommunicationBridgeConfig,
        )

        values = await self._resolve_bridge_fields(
            "communication",
            (
                ("bus_bridge_poll_timeout_seconds", "float"),
                ("bus_bridge_max_consecutive_errors", "int"),
                ("webhook_bridge_poll_timeout_seconds", "float"),
                ("webhook_bridge_max_consecutive_errors", "int"),
                ("nats_history_batch_size", "int"),
                ("nats_history_fetch_timeout_seconds", "float"),
                ("delegation_record_store_max_size", "int"),
                ("event_stream_max_queue_size", "int"),
                ("loop_prevention_window_seconds", "float"),
            ),
        )
        return CommunicationBridgeConfig(**values)

    async def get_a2a_bridge_config(self) -> A2ABridgeConfig:
        """Assemble ``A2ABridgeConfig`` from bridged A2A settings.

        Returns:
            An ``A2ABridgeConfig`` populated with the bridged A2A
            settings resolved from the settings service.
        """
        from synthorg.settings.bridge_configs import A2ABridgeConfig  # noqa: PLC0415

        values = await self._resolve_bridge_fields(
            "a2a",
            (
                ("client_timeout_seconds", "float"),
                ("push_verification_clock_skew_seconds", "int"),
                ("max_message_parts", "int"),
            ),
        )
        return A2ABridgeConfig(**values)

    async def get_engine_bridge_config(self) -> EngineBridgeConfig:
        """Assemble ``EngineBridgeConfig`` from bridged engine settings.

        Returns:
            An ``EngineBridgeConfig`` populated with the bridged engine
            settings resolved from the settings service.
        """
        from synthorg.settings.bridge_configs import EngineBridgeConfig  # noqa: PLC0415

        values = await self._resolve_bridge_fields(
            "engine",
            (
                ("approval_interrupt_timeout_seconds", "float"),
                ("max_subworkflow_depth", "int"),
                ("health_quality_degradation_threshold", "int"),
                ("routing_weight_primary_skill", "float"),
                ("routing_weight_secondary_skill", "float"),
                ("routing_weight_tag_match_bonus", "float"),
                ("routing_weight_role_match_bonus", "float"),
                ("routing_weight_seniority_alignment_bonus", "float"),
                ("routing_min_score", "float"),
                ("matcher_tier_base_score", "float"),
                ("matcher_headroom_max_bonus", "float"),
                ("matcher_priority_max_bonus", "float"),
                ("matcher_headroom_ratio_cap", "float"),
                ("matcher_balanced_partial_credit", "float"),
                ("quality_heuristic_pass_threshold", "float"),
                ("quality_heuristic_pass_grade", "float"),
                ("quality_heuristic_fail_grade", "float"),
                ("quality_heuristic_confidence_ceiling", "float"),
                ("quality_heuristic_confidence_bias", "float"),
            ),
        )
        return EngineBridgeConfig(**values)

    async def get_client_bridge_config(self) -> ClientBridgeConfig:
        """Assemble ``ClientBridgeConfig`` from bridged client settings.

        Returns:
            A ``ClientBridgeConfig`` populated with the bridged client
            settings resolved from the settings service.
        """
        from synthorg.settings.bridge_configs import ClientBridgeConfig  # noqa: PLC0415

        values = await self._resolve_bridge_fields(
            "client",
            (
                ("scored_feedback_passing_score", "float"),
                ("scored_feedback_strictness_multiplier", "float"),
                ("scored_feedback_strictness_floor", "float"),
            ),
        )
        return ClientBridgeConfig(**values)

    async def get_memory_bridge_config(self) -> MemoryBridgeConfig:
        """Assemble ``MemoryBridgeConfig`` from bridged memory settings.

        Returns:
            A ``MemoryBridgeConfig`` populated with the bridged memory
            settings and the validated VRAM-to-batch-size lookup table.

        Raises:
            ValueError: If ``fine_tune_vram_batch_table`` is not a list
                of two-element ``[vram_gb, batch_size]`` pairs, any pair
                has ``vram_gb <= 0`` or ``batch_size < 1``, the table is
                not strictly descending by ``vram_gb``, or a batch-size
                entry is a fractional float.
        """
        from synthorg.settings.bridge_configs import MemoryBridgeConfig  # noqa: PLC0415

        values = await self._resolve_bridge_fields(
            "memory",
            (
                ("consolidation_enforce_batch_size", "int"),
                ("fine_tune_chunk_size", "int"),
            ),
        )
        # ``get_json`` parses the value and emits the structured
        # ``SETTINGS_VALIDATION_FAILED`` warning on JSON-decode errors,
        # keeping this setting on the same observability path as every
        # other JSON-typed setting in the resolver.
        parsed = await self.get_json("memory", "fine_tune_vram_batch_table")
        if not isinstance(parsed, list) or any(
            not isinstance(row, list | tuple) or len(row) != 2  # noqa: PLR2004 -- pair shape
            for row in parsed
        ):
            msg = (
                "memory.fine_tune_vram_batch_table must be a JSON array of "
                f"[vram_gb, batch_size] pairs; got {parsed!r}"
            )
            raise ValueError(msg)
        try:
            table = tuple(
                (_coerce_vram_gb(vram_gb), _coerce_batch_size(batch_size))
                for vram_gb, batch_size in parsed
            )
        except (TypeError, ValueError) as exc:
            msg = (
                "memory.fine_tune_vram_batch_table must contain numeric "
                f"[vram_gb, batch_size] pairs; got {parsed!r}"
            )
            raise ValueError(msg) from exc
        if any(vram_gb <= 0.0 or batch_size < 1 for vram_gb, batch_size in table):
            msg = (
                "memory.fine_tune_vram_batch_table requires vram_gb > 0 and "
                f"batch_size >= 1; got {table!r}"
            )
            raise ValueError(msg)
        if any(table[i][0] <= table[i + 1][0] for i in range(len(table) - 1)):
            msg = (
                "memory.fine_tune_vram_batch_table must be strictly descending "
                f"by vram_gb so threshold selection is unambiguous; got {table!r}"
            )
            raise ValueError(msg)
        values["fine_tune_vram_batch_table"] = table
        return MemoryBridgeConfig(**values)

    async def get_integrations_bridge_config(self) -> IntegrationsBridgeConfig:
        """Assemble ``IntegrationsBridgeConfig`` from bridged settings.

        Returns:
            An ``IntegrationsBridgeConfig`` populated with the bridged
            integrations settings resolved from the settings service.
        """
        from synthorg.settings.bridge_configs import (  # noqa: PLC0415
            IntegrationsBridgeConfig,
        )

        values = await self._resolve_bridge_fields(
            "integrations",
            (
                ("health_probe_interval_seconds", "int"),
                ("oauth_http_timeout_seconds", "float"),
                ("oauth_device_flow_max_wait_seconds", "int"),
                ("rate_limit_coordinator_poll_timeout_seconds", "float"),
            ),
        )
        return IntegrationsBridgeConfig(**values)

    async def get_meta_bridge_config(self) -> MetaBridgeConfig:
        """Assemble ``MetaBridgeConfig`` from bridged meta settings.

        Returns:
            A ``MetaBridgeConfig`` populated with the bridged meta
            settings resolved from the settings service.
        """
        from synthorg.settings.bridge_configs import MetaBridgeConfig  # noqa: PLC0415

        values = await self._resolve_bridge_fields(
            "meta",
            (
                ("ci_timeout_seconds", "int"),
                ("proposal_rate_limit_max", "int"),
                ("outcome_store_default_limit", "int"),
            ),
        )
        return MetaBridgeConfig(**values)

    async def get_notifications_bridge_config(self) -> NotificationsBridgeConfig:
        """Assemble ``NotificationsBridgeConfig`` from bridged settings.

        Returns:
            A ``NotificationsBridgeConfig`` populated with the bridged
            notifications settings resolved from the settings service.
        """
        from synthorg.settings.bridge_configs import (  # noqa: PLC0415
            NotificationsBridgeConfig,
        )

        values = await self._resolve_bridge_fields(
            "notifications",
            (
                ("slack_webhook_timeout_seconds", "float"),
                ("ntfy_webhook_timeout_seconds", "float"),
                ("email_smtp_timeout_seconds", "float"),
                ("slack_default_webhook_url", "str"),
                ("ntfy_default_url", "str"),
            ),
        )
        return NotificationsBridgeConfig(**values)

    async def get_tools_bridge_config(self) -> ToolsBridgeConfig:
        """Assemble ``ToolsBridgeConfig`` from bridged tool settings.

        Returns:
            A ``ToolsBridgeConfig`` populated with the bridged tool
            settings resolved from the settings service.
        """
        from synthorg.settings.bridge_configs import ToolsBridgeConfig  # noqa: PLC0415

        values = await self._resolve_bridge_fields(
            "tools",
            (
                ("git_kill_grace_timeout_seconds", "float"),
                ("docker_sidecar_health_poll_interval_seconds", "float"),
                ("docker_sidecar_health_timeout_seconds", "float"),
                ("docker_sidecar_memory_limit", "str"),
                ("docker_sidecar_cpu_limit", "float"),
                ("docker_sidecar_max_pids", "int"),
                ("docker_stop_grace_timeout_seconds", "int"),
                ("subprocess_kill_grace_timeout_seconds", "float"),
            ),
        )
        return ToolsBridgeConfig(**values)

    async def get_observability_bridge_config(self) -> ObservabilityBridgeConfig:
        """Assemble ``ObservabilityBridgeConfig`` from bridged settings.

        Returns:
            An ``ObservabilityBridgeConfig`` populated with the bridged
            observability settings resolved from the settings service.
        """
        from synthorg.settings.bridge_configs import (  # noqa: PLC0415
            ObservabilityBridgeConfig,
        )

        values = await self._resolve_bridge_fields(
            "observability",
            (
                ("http_batch_size", "int"),
                ("http_flush_interval_seconds", "float"),
                ("http_timeout_seconds", "float"),
                ("http_max_retries", "int"),
                ("audit_chain_signing_timeout_seconds", "float"),
                ("tsa_endpoint_freetsa", "str"),
                ("tsa_endpoint_digicert", "str"),
                ("tsa_endpoint_sectigo", "str"),
            ),
        )
        return ObservabilityBridgeConfig(**values)

    async def get_settings_dispatcher_bridge_config(
        self,
    ) -> SettingsDispatcherBridgeConfig:
        """Assemble ``SettingsDispatcherBridgeConfig`` from bridged settings.

        Returns:
            A ``SettingsDispatcherBridgeConfig`` populated with the
            bridged settings-dispatcher settings resolved from the
            settings service.
        """
        from synthorg.settings.bridge_configs import (  # noqa: PLC0415
            SettingsDispatcherBridgeConfig,
        )

        values = await self._resolve_bridge_fields(
            "settings",
            (
                ("dispatcher_poll_timeout_seconds", "float"),
                ("dispatcher_error_backoff_seconds", "float"),
                ("dispatcher_max_consecutive_errors", "int"),
            ),
        )
        # Field names on the dataclass are short (poll_timeout_seconds etc.);
        # translate from the namespaced key form.
        return SettingsDispatcherBridgeConfig(
            poll_timeout_seconds=values["dispatcher_poll_timeout_seconds"],
            error_backoff_seconds=values["dispatcher_error_backoff_seconds"],
            max_consecutive_errors=values["dispatcher_max_consecutive_errors"],
        )
