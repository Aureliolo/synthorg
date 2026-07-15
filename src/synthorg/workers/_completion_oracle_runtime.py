"""Completion-oracle peer-review runtime construction for the worker boot path.

Split out of :mod:`runtime_builder` so that orchestrator stays focused on the
overall worker/coordinator wiring. Resolves the oracle's behaviour config and
reviewer model tier from settings, pins the reviewer agent to the active
provider, and sources the durable verdict archive from the connected
persistence backend.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.task_enums import Stakes
from synthorg.core.types import ModelTier
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.completion_oracle.builder import (
    CompletionOracleRuntime,
    CompletionOracleToolSeed,
    build_completion_oracle_runtime,
)
from synthorg.engine.completion_oracle.config import CompletionOracleConfig
from synthorg.engine.completion_oracle.evaluator import BuildTestOracle
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.completion_oracle import (
    COMPLETION_ORACLE_CONFIG_RESOLVE_FAILED,
    COMPLETION_ORACLE_GATES_WIRED,
    COMPLETION_ORACLE_REVIEWER_TIER_FALLBACK,
)
from synthorg.persistence.state import (
    code_execution_records_of,
    completion_oracle_reports_of,
)
from synthorg.settings.errors import SettingsError
from synthorg.settings.state import config_resolver_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

# Vendor-agnostic reviewer model ids per tier; operators override via the
# post-init provider swap path. Mirrors the red-team agent's model convention.
_TIER_MODEL_IDS: Final[MappingProxyType[ModelTier, str]] = MappingProxyType(
    {
        "small": "example-small-001",
        "medium": "example-medium-001",
        "large": "example-large-001",
    }
)
_DEFAULT_REVIEWER_TIER: Final[ModelTier] = "medium"


async def resolve_completion_oracle_config(
    app_state: AppState,
) -> CompletionOracleConfig:
    """Resolve the oracle's behaviour config from settings.

    Tolerates a settings-store outage by falling back to the on-by-default
    config: a transient read failure must not silently disable the oracle.

    Returns:
        The resolved :class:`CompletionOracleConfig`.
    """
    resolver = config_resolver_of(app_state)
    try:
        enabled = await resolver.get_bool("engine", "completion_oracle_enabled")
        shadow = await resolver.get_bool("engine", "completion_oracle_shadow_mode")
        min_stakes = await resolver.get_enum(
            "engine", "completion_oracle_min_stakes", Stakes
        )
        # Construct inside the try so a malformed value (rejected by the frozen
        # model's validators, a ValueError) also falls back to the safe default
        # rather than crashing boot; ``ValidationError`` is a ``ValueError``.
        return CompletionOracleConfig(
            enabled=enabled, shadow_mode=shadow, min_stakes=min_stakes
        )
    except (SettingsError, ValueError) as exc:
        # Distinct from GATE_SKIPPED: the fallback config keeps the oracle
        # ENABLED, so a settings-store outage must not read as a deliberate
        # skip on an operator's "is the gate running?" dashboard.
        logger.warning(
            COMPLETION_ORACLE_CONFIG_RESOLVE_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="using on-by-default completion-oracle config",
        )
        return CompletionOracleConfig()


async def build_completion_oracle_runtime_or_none(
    *,
    app_state: AppState,
    engine: AgentEngine,
    provider_name: str,
    seed: CompletionOracleToolSeed,
    config: CompletionOracleConfig,
) -> CompletionOracleRuntime | None:
    """Construct the peer-review runtime when the oracle is enabled.

    Resolves the reviewer's model tier from settings and pins the reviewer
    :class:`ModelConfig` to the active provider with the tier's vendor-agnostic
    model id. The ``seed`` carries the per-boot verdict repo + submit tool
    already registered on the engine's tool registry, so the runtime shares
    those instances. The durable verdict archive is sourced from the connected
    persistence backend (``None`` in a persistence-less boot).

    Returns:
        The :class:`CompletionOracleRuntime` when enabled, otherwise ``None``.
    """
    from synthorg.core.agent import ModelConfig  # noqa: PLC0415

    if not config.enabled:
        return None
    tier = await _resolve_reviewer_tier(app_state)
    model = ModelConfig(
        provider=provider_name,
        model_id=_TIER_MODEL_IDS[tier],
    )
    return build_completion_oracle_runtime(
        config=config,
        engine=engine,
        model=model,
        seed=seed,
        report_archive=completion_oracle_reports_of(app_state),
        clock=app_state.clock,
    )


async def _resolve_reviewer_tier(app_state: AppState) -> ModelTier:
    """Resolve the reviewer model tier from settings, falling back to medium.

    Returns:
        The configured reviewer tier, or ``medium`` on a read failure or an
        unrecognised value (never silently inheriting a cheaper tier).
    """
    try:
        raw = await config_resolver_of(app_state).get_str(
            "engine", "completion_oracle_reviewer_model_tier"
        )
    except (SettingsError, ValueError) as exc:
        logger.warning(
            COMPLETION_ORACLE_REVIEWER_TIER_FALLBACK,
            reason="config_resolve_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback_tier=_DEFAULT_REVIEWER_TIER,
        )
        return _DEFAULT_REVIEWER_TIER
    for tier in _TIER_MODEL_IDS:
        if tier == raw:
            return tier
    # An operator typo / stale tier value must not silently downgrade the
    # reviewer to the default; log it so the misconfiguration is visible.
    logger.warning(
        COMPLETION_ORACLE_REVIEWER_TIER_FALLBACK,
        reason="unrecognised_tier_value",
        configured_value=raw,
        fallback_tier=_DEFAULT_REVIEWER_TIER,
    )
    return _DEFAULT_REVIEWER_TIER


def attach_completion_oracle_gates(
    app_state: AppState,
    *,
    enabled: bool,
    completion_oracle_runtime: CompletionOracleRuntime | None,
) -> None:
    """Attach (or clear) the build/test and peer-review gates on the review gate.

    The single seam both the startup wiring and the hot-reload path call, so a
    settings edit re-attaches the gates to the persistent review-gate service on
    the next task (no restart). The two gates are wired *independently*: the
    deterministic build/test gate needs no provider, so it attaches whenever the
    oracle is ``enabled`` regardless of whether the provider-backed peer runtime
    could be built; the peer-review gate attaches only when its
    ``completion_oracle_runtime`` is present. A provider-less / degraded boot
    therefore still fails closed on unverified code tasks. A no-op when no
    review gate is wired (persistence-less boot).

    Args:
        app_state: Application state holding the review gate + record store.
        enabled: Whether the completion oracle is enabled (drives the
            deterministic build/test gate, which needs no provider).
        completion_oracle_runtime: The rebuilt peer-review bundle, or ``None``
            when the oracle is disabled or no provider is configured.
    """
    review_gate_service = app_state.slice(ApprovalStateSlice).review_gate
    if review_gate_service is None:
        return
    if enabled:
        review_gate_service.set_build_test_gate(
            BuildTestOracle(),
            records=code_execution_records_of(app_state),
        )
    else:
        review_gate_service.set_build_test_gate(None, records=None)
    if completion_oracle_runtime is None:
        review_gate_service.set_completion_oracle_gate(
            None, shadow_mode=False, min_stakes=Stakes.LOW
        )
        # Observability for a hot-reload toggle: a later disable / provider loss
        # would otherwise leave no trace beyond the initial boot state.
        logger.info(
            COMPLETION_ORACLE_GATES_WIRED,
            build_test_attached=enabled,
            peer_review_attached=False,
        )
        return
    review_gate_service.set_completion_oracle_gate(
        completion_oracle_runtime.gate,
        shadow_mode=completion_oracle_runtime.shadow_mode,
        min_stakes=completion_oracle_runtime.min_stakes,
    )
    logger.info(
        COMPLETION_ORACLE_GATES_WIRED,
        build_test_attached=enabled,
        peer_review_attached=True,
        shadow_mode=completion_oracle_runtime.shadow_mode,
        min_stakes=completion_oracle_runtime.min_stakes.value,
    )
