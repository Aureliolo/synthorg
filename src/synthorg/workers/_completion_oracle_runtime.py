"""Completion-oracle peer-review runtime construction for the worker boot path.

Split out of :mod:`runtime_builder` so that orchestrator stays focused on the
overall worker/coordinator wiring. Resolves the oracle's behaviour config and
reviewer model tier from settings, pins the reviewer agent to the active
provider, and sources the durable verdict archive from the connected
persistence backend.
"""

from typing import TYPE_CHECKING, Final

from synthorg.core.task_enums import Stakes
from synthorg.core.types import ModelTier
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.completion_oracle.builder import (
    CompletionOracleRuntime,
    CompletionOracleToolSeed,
    build_completion_oracle_runtime,
)
from synthorg.engine.completion_oracle.config import CompletionOracleConfig
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.completion_oracle import (
    COMPLETION_ORACLE_GATE_SKIPPED,
)
from synthorg.persistence.state import completion_oracle_reports_of
from synthorg.settings.errors import SettingsError
from synthorg.settings.state import config_resolver_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

# Vendor-agnostic reviewer model ids per tier; operators override via the
# post-init provider swap path. Mirrors the red-team agent's model convention.
_TIER_MODEL_IDS: Final[dict[ModelTier, str]] = {
    "small": "example-small-001",
    "medium": "example-medium-001",
    "large": "example-large-001",
}
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
        logger.warning(
            COMPLETION_ORACLE_GATE_SKIPPED,
            reason="config_resolve_failed",
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
    except SettingsError, ValueError:
        return _DEFAULT_REVIEWER_TIER
    if raw in _TIER_MODEL_IDS:
        return raw
    return _DEFAULT_REVIEWER_TIER
