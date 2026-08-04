"""Live-config resolution mixin for the self-improvement service.

Holds the per-cycle / per-call settings reads that make the meta-loop
hot-reconfigurable: the toggleable-strategy altitude snapshot, the lazy
Chief-of-Staff learning components, and the proposal-analysis model seam.
The cycle orchestration lives in ``service``; this mixin owns the live
config surface so ``service`` stays under its module-size budget.
"""

from typing import Final

from pydantic import BaseModel, ConfigDict

from synthorg.core.types import NotBlankStr
from synthorg.memory.protocol import MemoryBackend
from synthorg.meta.chief_of_staff._capability_gate import resolve_cos_autonomous_cap
from synthorg.meta.chief_of_staff.outcome_store import MemoryBackendOutcomeStore
from synthorg.meta.chief_of_staff.protocol import ConfidenceAdjuster
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.factory import build_confidence_adjuster
from synthorg.meta.models import ProposalAltitude
from synthorg.meta.protocol import ImprovementStrategy
from synthorg.observability import get_logger
from synthorg.observability.events.chief_of_staff import (
    COS_LEARNING_ENABLED,
    COS_OUTCOME_RECORD_FAILED,
)
from synthorg.observability.events.meta import META_CYCLE_FAILED
from synthorg.settings.bound_model import resolve_bound_model_live
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.kill_switch import (
    require_configured_model,
    resolve_bool_with_fallback,
)
from synthorg.settings.model_ref import ModelRef
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_ANALYSIS_MODEL_KEY: Final[str] = "analysis_model"

# Strategy altitudes whose ``self_improvement`` toggle is read live each
# cycle. ``ProposalAltitude.CODE_MODIFICATION`` is deliberately absent: it
# stays restart-bound (its strategy is built only when baked-enabled) and is
# never live-promoted. The toggle key matches the ``SelfImprovementConfig``
# field name, so the baked fallback is ``getattr(config, key)``.
_LIVE_STRATEGY_TOGGLES: Final[tuple[tuple[ProposalAltitude, str], ...]] = (
    (ProposalAltitude.CONFIG_TUNING, "config_tuning_enabled"),
    (ProposalAltitude.ARCHITECTURE, "architecture_proposals_enabled"),
    (ProposalAltitude.PROMPT_TUNING, "prompt_tuning_enabled"),
)
_TOGGLEABLE_ALTITUDES: Final[frozenset[ProposalAltitude]] = frozenset(
    altitude for altitude, _ in _LIVE_STRATEGY_TOGGLES
)


class AnalysisSettings(BaseModel):
    """Live proposal-analysis binding + sampling parameters.

    The ``(provider, model)`` pair is read live per call so an operator can
    retarget analysis without a restart; the sampling parameters come from the
    baked structural config. No strategy consumes this yet -- it is the live
    seam an LLM analysis pass will read once one is added.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    model: ModelRef
    temperature: float
    max_tokens: int


class SelfImprovementLiveConfigMixin:
    """Per-cycle / per-call settings reads for the self-improvement service.

    Relies on the concrete :class:`SelfImprovementService` to supply the
    config, live resolver, learning components, and strategy tuple.
    """

    _config: SelfImprovementConfig
    _config_resolver: ConfigResolver | None
    _memory_backend: MemoryBackend | None
    _outcome_store: MemoryBackendOutcomeStore | None
    _confidence_adjuster: ConfidenceAdjuster | None
    _learning_no_backend_warned: bool

    def _ensure_learning_components(self) -> None:
        """Build the outcome store + confidence adjuster on demand.

        Idempotent: a no-op once the adjuster exists, so a runtime enable
        builds the pair exactly once and later cycles reuse them. Warns once
        and leaves the components unset when no memory backend is wired
        (learning cannot persist outcomes without one); the warn-once latch
        keeps a persistently backend-less loop from re-logging every cycle.
        """
        if self._confidence_adjuster is not None:
            return
        if self._memory_backend is None:
            if not self._learning_no_backend_warned:
                self._learning_no_backend_warned = True
                logger.warning(
                    COS_OUTCOME_RECORD_FAILED,
                    reason="learning_enabled_but_no_memory_backend",
                )
            return
        self._outcome_store = MemoryBackendOutcomeStore(
            backend=self._memory_backend,
            agent_id=NotBlankStr("chief-of-staff"),
            min_outcomes=self._config.chief_of_staff.min_outcomes,
        )
        self._confidence_adjuster = build_confidence_adjuster(self._config)
        logger.info(
            COS_LEARNING_ENABLED,
            strategy=self._config.chief_of_staff.adjuster_strategy,
        )

    async def _learning_active(self) -> bool:
        """Resolve whether confidence learning is live this cycle.

        Reads the persona master switch and ``chief_of_staff.learning_enabled``
        live (falling back to the baked config), and lazily builds the learning
        components when the capability is on so a runtime enable takes effect
        on the next cycle.

        Returns:
            ``True`` when the meta-loop should adjust proposal confidence.
        """
        active = await resolve_cos_autonomous_cap(
            resolver=self._config_resolver,
            key="learning_enabled",
            master_fallback=self._config.chief_of_staff_enabled,
            cap_fallback=self._config.chief_of_staff.learning_enabled,
        )
        if active:
            self._ensure_learning_components()
        return active

    async def _resolve_enabled_altitudes(self) -> frozenset[ProposalAltitude]:
        """Snapshot which toggleable strategy altitudes are live this cycle.

        Read once at the top of a cycle so a concurrent settings swap cannot
        change the active strategy set mid-cycle (captured-reference
        semantics). Each toggle falls back to the baked config value when no
        resolver is wired.

        Returns:
            The set of toggleable altitudes enabled for this cycle.
        """
        enabled: set[ProposalAltitude] = set()
        for altitude, key in _LIVE_STRATEGY_TOGGLES:
            if await resolve_bool_with_fallback(
                resolver=self._config_resolver,
                namespace=SettingNamespace.SELF_IMPROVEMENT,
                key=key,
                fallback=bool(getattr(self._config, key)),
            ):
                enabled.add(altitude)
        return frozenset(enabled)

    def _strategy_active(
        self,
        strategy: ImprovementStrategy,
        enabled_altitudes: frozenset[ProposalAltitude],
    ) -> bool:
        """Whether *strategy* may run given this cycle's enabled altitudes.

        Toggleable altitudes run only when their flag is live-enabled;
        non-toggleable altitudes (code modification) are present only when
        baked-enabled and always pass the filter.

        Returns:
            ``True`` when the strategy should participate this cycle.
        """
        if strategy.altitude in _TOGGLEABLE_ALTITUDES:
            return strategy.altitude in enabled_altitudes
        return True

    async def resolve_analysis_settings(self) -> AnalysisSettings:
        """Resolve the live proposal-analysis model + sampling parameters.

        The model is read live from ``self_improvement.analysis_model`` so an
        operator can retarget analysis without a restart; the sampling
        parameters come from the baked structural config. No strategy consumes
        this yet -- it is the live seam an LLM analysis pass will read once one
        is added.

        Returns:
            The resolved analysis settings.
        """
        return AnalysisSettings(
            model=require_configured_model(
                await resolve_bound_model_live(
                    self._config_resolver,
                    namespace=SettingNamespace.SELF_IMPROVEMENT,
                    key=_ANALYSIS_MODEL_KEY,
                    unset_event=META_CYCLE_FAILED,
                ),
                namespace=SettingNamespace.SELF_IMPROVEMENT,
                key=_ANALYSIS_MODEL_KEY,
                feature_label="Proposal analysis",
            ),
            temperature=self._config.analysis_temperature,
            max_tokens=self._config.analysis_max_tokens,
        )


__all__ = ["AnalysisSettings", "SelfImprovementLiveConfigMixin"]
