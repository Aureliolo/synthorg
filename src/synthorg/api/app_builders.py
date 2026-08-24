"""Config bootstrap and subsystem builders for the Litestar application.

Second-half wiring helpers for the Litestar application: logging
bootstrap, memory-dir resolution, telemetry collector, performance
tracker, and LLM-judge resolution. Kept out of :mod:`synthorg.api.app`
so the composition root stays a thin orchestrator.
"""

import os
import tempfile
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

from synthorg.api._feature_provider_resolution import resolve_feature_provider
from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.config import DEFAULT_SINKS, LogConfig
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_MEMORY_DIR_TMPROOT_FALLBACK,
)
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.enums import SettingNamespace, SettingSource
from synthorg.settings.mirrors import parse_bool
from synthorg.telemetry import TelemetryCollector, TelemetryConfig

# All four of ``CostTracker`` / ``ChiefOfStaffChat`` / ``ChiefOfStaffConfig``
# / ``ProviderRegistry`` are imported lazily under TYPE_CHECKING.  Hoisting
# them to runtime imports created a circular import via the budget /
# observability chain (``cannot import name 'CostRecord' from partially
# initialized module 'synthorg.budget.cost_record'``).  Under PEP 649 the
# annotations are stored as code objects and only evaluated when
# ``typing.get_type_hints()`` runs against this module -- which Litestar's
# route discovery does for handler signatures, not for the helpers below
# (private prefix or non-handler).  ``ChiefOfStaffChat`` is also imported
# in-function below for the constructor call site, so the runtime
# constructor reference is independent of the annotation surface.
if TYPE_CHECKING:
    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.budget.tracker_protocol import CostTrackerProtocol
    from synthorg.config.schema import RootConfig
    from synthorg.core.clock import Clock
    from synthorg.hr.performance.config import PerformanceConfig
    from synthorg.hr.performance.tracker import PerformanceTracker
    from synthorg.meta.chief_of_staff.chat import ChiefOfStaffChat
    from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
    from synthorg.meta.chief_of_staff.propose import ChiefOfStaffProposer
    from synthorg.meta.chief_of_staff.routing import RoleRouter
    from synthorg.persistence.conversational_factory import (
        ConversationalRepositories,
    )
    from synthorg.providers.registry import ProviderRegistry
    from synthorg.security.autonomy.models import AutonomyConfig
    from synthorg.security.autonomy.protocol import AutonomyChangeStrategy
    from synthorg.security.autonomy.signals import RiskBudgetSignalProvider
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_DEFAULT_MEMORY_DIR = Path("/data/memory")


def _bootstrap_app_logging(effective_config: RootConfig) -> RootConfig:
    """Activate the structured logging pipeline.

    Resolves ``observability.log_directory`` via bootstrap_resolver
    (env > default; the directory is a mount the deployment fixes). When an
    env override is supplied, path-traversal is rejected before patching the
    live config.

    Returns:
        ``RootConfig`` instance.

    Raises:
        ValueError: Raised on the corresponding failure path.
    """
    from synthorg.config.loader import bootstrap_logging  # noqa: PLC0415

    resolved = resolve_init_value(
        SettingNamespace.OBSERVABILITY,
        "log_directory",
    )
    if resolved.source != SettingSource.ENVIRONMENT:
        bootstrap_logging(effective_config)
        return effective_config

    log_dir = str(resolved.value)
    if ".." in PurePath(log_dir).parts:
        msg = f"SYNTHORG_LOG_DIR contains '..' path traversal component: {log_dir!r}"
        raise ValueError(msg)

    base_log_cfg = effective_config.logging or LogConfig(
        sinks=DEFAULT_SINKS,
    )
    patched = effective_config.model_copy(
        update={
            "logging": base_log_cfg.model_copy(
                update={"log_dir": log_dir},
            ),
        },
    )
    bootstrap_logging(patched)
    return patched


def build_chief_of_staff_chat(
    chief_of_staff_config: ChiefOfStaffConfig,
    *,
    provider_registry: ProviderRegistry,
    cost_tracker: CostTrackerProtocol | None,
    config_resolver: ConfigResolver | None = None,
) -> ChiefOfStaffChat | None:
    """Resolve a ChiefOfStaffChat from the meta config + provider registry.

    Ghost-wired: built whenever an LLM provider is registered, independent
    of the enablement flag. The live per-request gate on ``POST /meta/chat``
    (``ensure_feature_enabled(..., "explain_chat_enabled")``) is the sole
    enablement gate, so toggling the setting takes effect on the next
    request with no restart. Returns ``None`` -- and the endpoint then
    surfaces 503 -- only when no LLM provider is registered (degenerate
    test/anonymous boots).

    The provider is resolved by the configured chat model (the model
    name in config is provider-agnostic).

    Returns:
        The ``ChiefOfStaffChat`` value when present, ``None`` otherwise.
    """
    from synthorg.meta.chief_of_staff.chat import ChiefOfStaffChat  # noqa: PLC0415

    provider = resolve_feature_provider(
        provider_registry,
        chief_of_staff_config.chat_model,
        feature="chief_of_staff_chat",
    )
    if provider is None:
        return None
    logger.info(
        API_APP_STARTUP,
        note="Chief of Staff chat configured",
        chat_model=str(chief_of_staff_config.chat_model),
    )
    return ChiefOfStaffChat(
        # The live model setting names the connection to dispatch on, so the
        # service resolves it per call rather than holding whichever client
        # was resolved here at boot.
        connections=provider_registry.get,
        config=chief_of_staff_config,
        cost_tracker=cost_tracker,
        config_resolver=config_resolver,
    )


def build_chief_of_staff_proposer(  # noqa: PLR0913 -- DI builder seam
    chief_of_staff_config: ChiefOfStaffConfig,
    *,
    provider_registry: ProviderRegistry,
    approval_store: ApprovalStoreProtocol,
    repositories: ConversationalRepositories | None,
    cost_tracker: CostTrackerProtocol | None,
    clock: Clock | None = None,
    role_router: RoleRouter | None = None,
    config_resolver: ConfigResolver | None = None,
    master_enabled: bool = True,
) -> ChiefOfStaffProposer | None:
    """Resolve a ChiefOfStaffProposer from config + wiring.

    Returns ``None`` -- and ``POST /meta/chat/propose`` then surfaces
    503 -- when:

    - ``chief_of_staff_config.propose_enabled`` is False (opt-in
      default), or
    - no LLM provider is registered, or
    - the conversational repositories could not be built (persistence
      absent / not connected).

    The provider is resolved by the configured propose model (the model
    name in config is provider-agnostic). *provider_registry* is always
    forwarded to the
    proposer; *role_router* does not gate that forwarding, it only
    changes how the registry is used: when a router is supplied (concern
    routing) a routed turn is answered by the matched role agent on its
    own configured provider, and when it is absent the proposer stays in
    generic mode but still holds the registry.

    Returns:
        The ``ChiefOfStaffProposer`` value when present, ``None`` otherwise.
    """
    from synthorg.meta.chief_of_staff.propose import (  # noqa: PLC0415
        ChiefOfStaffProposer,
    )

    if not chief_of_staff_config.propose_enabled:
        return None
    if repositories is None:
        logger.warning(
            API_APP_STARTUP,
            note="Chief of Staff propose enabled but persistence unavailable",
        )
        return None
    provider = resolve_feature_provider(
        provider_registry,
        chief_of_staff_config.propose_model,
        feature="chief_of_staff_propose",
    )
    if provider is None:
        return None
    logger.info(
        API_APP_STARTUP,
        note="Chief of Staff propose configured",
        propose_model=str(chief_of_staff_config.propose_model),
    )
    return ChiefOfStaffProposer(
        config=chief_of_staff_config,
        conversation_repo=repositories.conversation_repo,
        turn_repo=repositories.turn_repo,
        approval_store=approval_store,
        clock=clock,
        cost_tracker=cost_tracker,
        role_router=role_router,
        connections=provider_registry.get,
        config_resolver=config_resolver,
        master_enabled=master_enabled,
    )


def _build_configured_autonomy_change_strategy(
    autonomy_config: AutonomyConfig,
    *,
    risk_budget_signal: RiskBudgetSignalProvider,
) -> AutonomyChangeStrategy:
    """Construct the configured autonomy-change strategy.

    Always returns a strategy (default ``kind=HUMAN_ONLY``): every
    promotion request then routes through human approval. ``HUMAN_ONLY``
    needs no signal provider; ``BUDGET_AWARE`` needs the risk-budget one,
    which is supplied here by the same :class:`RiskTracker` the budget
    slice records into, so choosing it is satisfiable rather than a
    configuration the factory can only reject.

    Returns:
        ``AutonomyChangeStrategy`` instance.
    """
    from synthorg.security.autonomy.change_strategy_config import (  # noqa: PLC0415
        AutonomyStrategyDeps,
    )
    from synthorg.security.autonomy.change_strategy_factory import (  # noqa: PLC0415
        build_autonomy_change_strategy,
    )

    return build_autonomy_change_strategy(
        autonomy_config.change_strategy,
        AutonomyStrategyDeps(risk_budget_signal=risk_budget_signal),
    )


def _allowed_memory_dir_roots() -> tuple[str, ...]:
    r"""Return the string roots a memory dir must begin with.

    Production containers mount the data volume at ``/data``, which
    is the only legitimate runtime base. Tests drive the builder
    with ``tmp_path``, so :func:`tempfile.gettempdir` is also
    admitted -- covering POSIX (``/tmp``, ``/var/tmp``) and Windows
    (``C:\Users\...\AppData\Local\Temp``) runners without special
    casing.

    Returns:
        Tuple of the declared element types.
    """
    roots: list[str] = [str(Path("/data"))]
    try:
        tmp_root: str | None = str(Path(tempfile.gettempdir()))
    except (OSError, RuntimeError) as exc:
        logger.warning(
            API_MEMORY_DIR_TMPROOT_FALLBACK,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        tmp_root = None
    if tmp_root is not None:
        roots.append(tmp_root)
    return tuple(roots)


def _resolve_memory_dir() -> Path:
    """Read and validate ``SYNTHORG_MEMORY_DIR`` for derived paths.

    Rejects empty, traversal, non-absolute, or outside-allowed-root
    values and falls back to :data:`_DEFAULT_MEMORY_DIR` with a warning.

    Returns:
        ``Path`` instance.
    """
    raw = os.environ.get("SYNTHORG_MEMORY_DIR")
    if raw is None:
        return _DEFAULT_MEMORY_DIR
    candidate = raw.strip()
    if not candidate:
        logger.warning(
            API_APP_STARTUP,
            detail="memory_dir_blank",
            reason="empty_or_whitespace",
        )
        return _DEFAULT_MEMORY_DIR
    path = Path(candidate)
    if ".." in path.parts:
        logger.warning(
            API_APP_STARTUP,
            detail="memory_dir_traversal",
            value=candidate,
        )
        return _DEFAULT_MEMORY_DIR
    if not path.is_absolute():
        logger.warning(
            API_APP_STARTUP,
            detail="memory_dir_not_absolute",
            value=candidate,
        )
        return _DEFAULT_MEMORY_DIR
    candidate_str = os.path.normcase(str(path))
    allowed_roots = _allowed_memory_dir_roots()
    if not any(
        candidate_str.startswith(os.path.normcase(root) + os.sep)
        for root in allowed_roots
    ):
        logger.warning(
            API_APP_STARTUP,
            detail="memory_dir_outside_allowed_roots",
            value=str(path),
            allowed=list(allowed_roots),
        )
        return _DEFAULT_MEMORY_DIR
    return path


_TELEMETRY_ENV_ACCEPTED: tuple[str, ...] = (
    "0",
    "1",
    "false",
    "no",
    "true",
    "yes",
)


def _resolve_telemetry_enabled(parsed: TelemetryConfig) -> TelemetryConfig:
    """Apply env-layer precedence for the registered ``telemetry.enabled`` setting.

    Reads ``telemetry.enabled`` via :func:`bootstrap_resolver.resolve_init_value`,
    which honours ``env > default`` for the registered env var (see
    :mod:`synthorg.settings.definitions.telemetry`). The collector is built
    in the construction phase, before persistence connects, so this only
    layers env over the code default. The authoritative DB layer is applied
    by the ``_apply_telemetry_db_layer`` on-startup hook in
    ``lifecycle_assembly``, which re-resolves with full DB > env > default
    precedence once the resolver is wired and before the collector starts;
    a later operator edit reaches the same setter through
    ``TelemetrySettingsSubscriber``.

    Validates the env value at this system boundary so a typo such as
    ``SYNTHORG_TELEMETRY_ENABLED=falsee`` raises rather than silently
    masking operator intent.

    Returns the (possibly updated) config.

    Raises:
        ValueError: When the env var is set to a value that is neither
            a truthy nor falsy token from the recognised vocabulary.

    Returns:
        ``TelemetryConfig`` instance.
    """
    resolved = resolve_init_value(
        SettingNamespace.TELEMETRY,
        "enabled",
        parse=parse_bool,
    )
    if resolved.source != SettingSource.ENVIRONMENT:
        env_raw = normalize_ascii_lowercase(
            os.environ.get("SYNTHORG_TELEMETRY_ENABLED", ""),
        )
        if env_raw:
            msg = (
                f"SYNTHORG_TELEMETRY_ENABLED must be one of"
                f" {list(_TELEMETRY_ENV_ACCEPTED)!r}; got {env_raw!r}."
                f" Refusing to silently fall back to the parsed value."
            )
            raise ValueError(msg)
        return parsed
    return parsed.model_copy(update={"enabled": bool(resolved.value)})


def _build_telemetry_collector(
    telemetry_cfg: TelemetryConfig | None = None,
) -> TelemetryCollector:
    """Build the project telemetry collector.

    Passing ``None`` for ``telemetry_cfg`` falls back to defaults
    (``enabled=False``). The env-layer override
    (``SYNTHORG_TELEMETRY_ENABLED``) is applied here via
    :func:`_resolve_telemetry_enabled` -- the collector itself takes
    the resolved boolean as-given. The same env name is registered as
    the ``telemetry.enabled`` setting's ``env_var_override`` so the
    /settings API and the boot path agree on a single source.

    Returns:
        ``TelemetryCollector`` instance.
    """
    memory_dir = _resolve_memory_dir()
    telemetry_dir = memory_dir.parent / "telemetry"
    parsed = telemetry_cfg if telemetry_cfg is not None else TelemetryConfig()
    config = _resolve_telemetry_enabled(parsed)
    return TelemetryCollector(config=config, data_dir=telemetry_dir)


def _build_performance_tracker(
    *,
    perf_config: PerformanceConfig | None = None,
) -> PerformanceTracker:
    """Build the task-metric ledger.

    Returns:
        ``PerformanceTracker`` instance.
    """
    from synthorg.hr.performance.config import (  # noqa: PLC0415
        PerformanceConfig,
    )
    from synthorg.hr.performance.tracker import (  # noqa: PLC0415
        PerformanceTracker,
    )

    return PerformanceTracker(config=perf_config or PerformanceConfig())
