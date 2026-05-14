"""Config bootstrap and subsystem builders for the Litestar application.

Collects the second-half wiring helpers that :mod:`synthorg.api.app`
used to inline: logging bootstrap, memory-dir resolution, telemetry
collector, performance tracker, and LLM-judge resolution.
"""

import os
import tempfile
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.config import DEFAULT_SINKS, LogConfig
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_MEMORY_DIR_TMPROOT_FALLBACK,
)
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
    from synthorg.budget.tracker import CostTracker
    from synthorg.config.schema import RootConfig
    from synthorg.hr.performance.config import PerformanceConfig
    from synthorg.hr.performance.quality_protocol import QualityScoringStrategy
    from synthorg.hr.performance.tracker import PerformanceTracker
    from synthorg.meta.chief_of_staff.chat import ChiefOfStaffChat
    from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
    from synthorg.providers.registry import ProviderRegistry
    from synthorg.security.trust.config import TrustConfig
    from synthorg.security.trust.service import TrustService

logger = get_logger(__name__)

_DEFAULT_MEMORY_DIR = Path("/data/memory")


def _bootstrap_app_logging(effective_config: RootConfig) -> RootConfig:
    """Activate the structured logging pipeline.

    Applies the ``SYNTHORG_LOG_DIR`` env var override (for Docker
    volume paths) before calling :func:`bootstrap_logging`.
    """
    from synthorg.config import bootstrap_logging  # noqa: PLC0415

    log_dir = os.environ.get("SYNTHORG_LOG_DIR", "").strip()
    if not log_dir:
        bootstrap_logging(effective_config)
        return effective_config

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


def _resolve_llm_judge_strategy(
    cfg: PerformanceConfig,
    *,
    provider_registry: ProviderRegistry,
    cost_tracker: CostTracker | None,
) -> QualityScoringStrategy | None:
    """Resolve the LLM judge strategy from config.

    Returns ``None`` if the judge model is not configured, the named
    provider is not registered, or no providers are available.
    """
    from synthorg.providers.errors import DriverNotRegisteredError  # noqa: PLC0415

    if cfg.quality_judge_model is None:
        return None

    judge_provider_name = cfg.quality_judge_provider
    if judge_provider_name is not None:
        try:
            provider_driver = provider_registry.get(str(judge_provider_name))
        except DriverNotRegisteredError:
            logger.warning(
                API_APP_STARTUP,
                note="Quality judge provider not found, LLM judge disabled",
                provider=str(judge_provider_name),
            )
            return None
    else:
        available = provider_registry.list_providers()
        if not available:
            logger.warning(
                API_APP_STARTUP,
                note="No providers available, LLM judge disabled",
            )
            return None
        provider_driver = provider_registry.get(available[0])

    from synthorg.hr.performance.llm_judge_quality_strategy import (  # noqa: PLC0415
        LlmJudgeQualityStrategy,
    )

    logger.info(
        API_APP_STARTUP,
        note="Quality LLM judge configured",
        model=str(cfg.quality_judge_model),
    )
    return LlmJudgeQualityStrategy(
        provider=provider_driver,
        model=cfg.quality_judge_model,
        cost_tracker=cost_tracker,
    )


def build_chief_of_staff_chat(
    chief_of_staff_config: ChiefOfStaffConfig,
    *,
    provider_registry: ProviderRegistry,
    cost_tracker: CostTracker | None,
) -> ChiefOfStaffChat | None:
    """Resolve a ChiefOfStaffChat from the meta config + provider registry.

    Returns ``None`` -- and the ``POST /meta/chat`` endpoint then surfaces
    503 -- when:

    - ``chief_of_staff_config.chat_enabled`` is False (the documented
      opt-in default), or
    - no LLM provider is registered (degenerate test/anonymous boots).

    The provider is picked by the same convention as the LLM quality
    judge: the first registered provider, since the chat model name in
    config is provider-agnostic.
    """
    from synthorg.meta.chief_of_staff.chat import ChiefOfStaffChat  # noqa: PLC0415

    if not chief_of_staff_config.chat_enabled:
        return None

    available = provider_registry.list_providers()
    if not available:
        logger.warning(
            API_APP_STARTUP,
            note="Chief of Staff chat enabled but no providers registered",
        )
        return None

    provider = provider_registry.get(available[0])
    logger.info(
        API_APP_STARTUP,
        note="Chief of Staff chat configured",
        provider=available[0],
        chat_model=str(chief_of_staff_config.chat_model),
    )
    return ChiefOfStaffChat(
        provider=provider,
        config=chief_of_staff_config,
        cost_tracker=cost_tracker,
    )


def _build_configured_trust_service(
    trust_config: TrustConfig,
) -> TrustService | None:
    """Construct a TrustService when trust is enabled, else return None.

    Returns ``None`` for the DISABLED strategy so callers skip the
    orchestrator entirely; controllers already treat
    ``trust_service`` as optional via ``AppState.has_trust_service``.
    """
    from synthorg.security.trust.factory import build_trust_strategy  # noqa: PLC0415
    from synthorg.security.trust.service import TrustService  # noqa: PLC0415

    strategy = build_trust_strategy(trust_config)
    if strategy is None:
        return None
    return TrustService(strategy=strategy, config=trust_config)


def _allowed_memory_dir_roots() -> tuple[str, ...]:
    r"""Return the string roots a memory dir must begin with.

    Production containers mount the data volume at ``/data``, which
    is the only legitimate runtime base. Tests drive the builder
    with ``tmp_path``, so :func:`tempfile.gettempdir` is also
    admitted -- covering POSIX (``/tmp``, ``/var/tmp``) and Windows
    (``C:\Users\...\AppData\Local\Temp``) runners without special
    casing.
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


_TELEMETRY_ENV_VAR = "SYNTHORG_TELEMETRY_ENABLED"
_TELEMETRY_ENV_TRUE = frozenset({"true", "1", "yes"})
_TELEMETRY_ENV_FALSE = frozenset({"false", "0", "no"})


def _resolve_telemetry_enabled(parsed: TelemetryConfig) -> TelemetryConfig:
    """Apply env-layer precedence for the registered ``telemetry.enabled`` setting.

    Single source of "env wins over YAML / default" for the boot
    path. ``SYNTHORG_TELEMETRY_ENABLED`` matches the env name registered
    on the ``telemetry.enabled`` setting (see
    ``synthorg.settings.definitions.telemetry``); when the value is
    set, it overrides the parsed ``TelemetryConfig.enabled`` field.
    The DB layer is consulted by ``SettingsService`` /
    ``ConfigResolver`` for runtime ``/settings`` reads and edits;
    those changes apply on the next process restart per the
    setting's ``restart_required`` semantics. The collector itself
    no longer re-applies this precedence so the audit trail stays
    single-sourced.

    Validates the env value at this system boundary -- a typo such as
    ``SYNTHORG_TELEMETRY_ENABLED=falsee`` would otherwise silently fall
    through to the parsed value and mask operator intent.

    Returns the (possibly updated) config.

    Raises:
        ValueError: When the env var is set to a value that is neither
            a truthy nor falsy token from the recognised vocabulary.
    """
    raw = normalize_ascii_lowercase(os.environ.get(_TELEMETRY_ENV_VAR, ""))
    if not raw:
        return parsed
    if raw in _TELEMETRY_ENV_TRUE:
        return parsed.model_copy(update={"enabled": True})
    if raw in _TELEMETRY_ENV_FALSE:
        return parsed.model_copy(update={"enabled": False})
    accepted = sorted(_TELEMETRY_ENV_TRUE | _TELEMETRY_ENV_FALSE)
    msg = (
        f"{_TELEMETRY_ENV_VAR} must be one of {accepted!r}; "
        f"got {raw!r}. Refusing to silently fall back to the parsed value."
    )
    raise ValueError(msg)


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
    """
    memory_dir = _resolve_memory_dir()
    telemetry_dir = memory_dir.parent / "telemetry"
    parsed = telemetry_cfg if telemetry_cfg is not None else TelemetryConfig()
    config = _resolve_telemetry_enabled(parsed)
    return TelemetryCollector(config=config, data_dir=telemetry_dir)


def _build_performance_tracker(
    *,
    cost_tracker: CostTracker | None = None,
    provider_registry: ProviderRegistry | None = None,
    perf_config: PerformanceConfig | None = None,
) -> PerformanceTracker:
    """Build a PerformanceTracker with composite quality strategy."""
    from synthorg.hr.performance.ci_quality_strategy import (  # noqa: PLC0415
        CISignalQualityStrategy,
    )
    from synthorg.hr.performance.composite_quality_strategy import (  # noqa: PLC0415
        CompositeQualityStrategy,
    )
    from synthorg.hr.performance.config import (  # noqa: PLC0415
        PerformanceConfig,
    )
    from synthorg.hr.performance.quality_override_store import (  # noqa: PLC0415
        QualityOverrideStore,
    )
    from synthorg.hr.performance.tracker import (  # noqa: PLC0415
        PerformanceTracker,
    )

    cfg = perf_config or PerformanceConfig()
    quality_override_store = QualityOverrideStore()

    llm_strategy = (
        _resolve_llm_judge_strategy(
            cfg,
            provider_registry=provider_registry,
            cost_tracker=cost_tracker,
        )
        if provider_registry is not None
        else None
    )

    composite = CompositeQualityStrategy(
        ci_strategy=CISignalQualityStrategy(),
        llm_strategy=llm_strategy,
        override_store=quality_override_store,
        ci_weight=cfg.quality_ci_weight,
        llm_weight=cfg.quality_llm_weight,
    )

    return PerformanceTracker(
        quality_strategy=composite,
        config=cfg,
        quality_override_store=quality_override_store,
    )
