"""Stakes-aware and flat routing strategies."""

from typing import TYPE_CHECKING

from synthorg.core.enums import Stakes, compare_stakes
from synthorg.core.types import ModelTier
from synthorg.engine.routing_policy.config import StakesRoutingConfig
from synthorg.engine.routing_policy.models import StakesRoutingDecision
from synthorg.engine.routing_policy.tiers import (
    TIER_LADDER,
    bump_one,
    higher_tier,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.stakes_routing import (
    STAKES_ROUTING_COORD_NUDGE,
    STAKES_ROUTING_TIER_UNRESOLVABLE,
)
from synthorg.providers.errors import ProviderError

if TYPE_CHECKING:
    from synthorg.budget.benchmark_protocol import BenchmarkScoreProvider
    from synthorg.budget.coordination_store import CoordinationMetricsStore
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task
    from synthorg.providers.routing.models import ResolvedModel
    from synthorg.providers.routing.resolver import ModelResolver

logger = get_logger(__name__)


class FlatStrategy:
    """No-op routing: keeps the agent's configured model, no red-team.

    This is today's behaviour. It is the control arm of the cost/quality
    comparison test and the opt-out selectable via the ``flat``
    discriminator.
    """

    async def route(
        self,
        *,
        task: Task,
        identity: AgentIdentity,
    ) -> StakesRoutingDecision:
        """Return a decision that leaves the model unchanged."""
        return StakesRoutingDecision(
            selected_model=identity.model,
            red_team_required=False,
            stakes=task.stakes,
            reason="flat routing: no stakes-based adjustment",
            source="flat",
        )


class StakesAwareStrategy:
    """Route by stakes, with a coordination nudge and a red-team mark.

    Picks the cheapest model tier whose benchmark score clears the
    per-stakes quality floor, bumps it up when recent coordination
    metrics look unhealthy, and marks high/critical work for the
    red-team gate. Deterministic given the injected benchmark scores and
    coordination records; performs no wall-clock reads or live provider
    calls.

    Args:
        benchmark_provider: Source of per-model quality scores.
        config: Per-stakes floors, nudge thresholds, and red-team
            threshold.
        resolver: Resolves a tier alias to a concrete model. When
            ``None``, the model cannot be adjusted; only the red-team
            mark is applied.
        coordination_store: Recent coordination metrics for the nudge.
            When ``None``, the nudge is skipped.
    """

    def __init__(
        self,
        *,
        benchmark_provider: BenchmarkScoreProvider,
        config: StakesRoutingConfig | None = None,
        resolver: ModelResolver | None = None,
        coordination_store: CoordinationMetricsStore | None = None,
    ) -> None:
        self._benchmark_provider = benchmark_provider
        self._config = config or StakesRoutingConfig()
        self._resolver = resolver
        self._coordination_store = coordination_store

    async def route(
        self,
        *,
        task: Task,
        identity: AgentIdentity,
    ) -> StakesRoutingDecision:
        """Pick a model tier matched to ``task.stakes`` (see class docstring).

        Returns:
            A :class:`StakesRoutingDecision` carrying the selected
            model, red-team requirement flag, stakes, reason, and
            source label.
        """
        stakes = task.stakes
        red_team_required = (
            compare_stakes(stakes, self._config.red_team_min_stakes) >= 0
        )
        current_tier = identity.model.model_tier

        floor = self._config.quality_floors.for_stakes(stakes)
        target_tier, floor_cleared = await self._cheapest_tier_meeting_floor(floor)

        nudged = False
        if target_tier is not None and self._coordination_unhealthy(task.id):
            bumped = bump_one(target_tier)
            if bumped != target_tier:
                logger.info(
                    STAKES_ROUTING_COORD_NUDGE,
                    task_id=task.id,
                    from_tier=target_tier,
                    to_tier=bumped,
                )
                nudged = True
            target_tier = bumped

        # Work at or above the configured red_team_min_stakes threshold
        # must never run below the agent's own tier.
        if red_team_required and target_tier is not None and current_tier is not None:
            target_tier = higher_tier(target_tier, current_tier)

        return self._build_decision(
            identity=identity,
            stakes=stakes,
            red_team_required=red_team_required,
            target_tier=target_tier,
            nudged=nudged,
            floor=floor,
            floor_cleared=floor_cleared,
        )

    def _build_decision(  # noqa: PLR0913 -- keyword-only assembly inputs
        self,
        *,
        identity: AgentIdentity,
        stakes: Stakes,
        red_team_required: bool,
        target_tier: ModelTier | None,
        nudged: bool,
        floor: float,
        floor_cleared: bool,
    ) -> StakesRoutingDecision:
        """Assemble the decision, resolving the target tier to a model.

        Returns:
            A :class:`StakesRoutingDecision` whose ``selected_model``
            is the resolved tier (when changed) or the agent's
            current model.
        """
        current = identity.model
        selected_model = current
        source = "stakes_aware:noop"
        reason = (
            f"stakes={stakes.value}: kept {current.model_tier or 'configured'} tier"
        )

        resolved = self._resolve_tier(target_tier) if target_tier is not None else None
        changed = (
            resolved is not None
            and target_tier is not None
            and (
                resolved.model_id != current.model_id
                or target_tier != current.model_tier
            )
        )
        if changed and resolved is not None and target_tier is not None:
            selected_model = current.model_copy(
                update={
                    "provider": resolved.provider_name,
                    "model_id": resolved.model_id,
                    "model_tier": target_tier,
                }
            )
            if nudged:
                source = "stakes_aware:nudge"
            elif not floor_cleared:
                source = "stakes_aware:floor_unmet"
            else:
                source = "stakes_aware:floor"
            reason = (
                f"stakes={stakes.value}: routed to {target_tier} tier (floor {floor:g})"
            )
            if not floor_cleared:
                reason += " [floor not met; strongest available tier]"

        return StakesRoutingDecision(
            selected_model=selected_model,
            red_team_required=red_team_required,
            stakes=stakes,
            reason=reason,
            source=source,
        )

    async def _cheapest_tier_meeting_floor(
        self, floor: float
    ) -> tuple[ModelTier | None, bool]:
        """Return the cheapest tier clearing *floor* and whether it cleared.

        The second element is ``True`` only when the returned tier's
        benchmark score meets the floor. When no resolvable tier clears
        the floor, the strongest resolvable tier is returned with
        ``False`` and a logged fallback, so high/critical work is never
        silently routed under-floor. ``(None, False)`` is returned when no
        tier resolves at all (no resolver wired, or the provider catalogue
        lacks the canonical tiers).

        A benchmark-provider failure for one tier is logged and skipped:
        retries belong to the provider layer, and a transient lookup error
        must not crash the routing decision for every task.
        """
        if self._resolver is None:
            return None, False
        strongest_resolvable: ModelTier | None = None
        strongest_score: float | None = None
        for tier in TIER_LADDER:
            resolved = self._resolver.resolve_safe(tier)
            if resolved is None:
                continue
            try:
                score = await self._benchmark_provider.get_score(resolved.model_id)
            except ProviderError as exc:
                logger.warning(
                    STAKES_ROUTING_TIER_UNRESOLVABLE,
                    floor=floor,
                    tier=tier,
                    reason="benchmark_lookup_failed",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                continue
            strongest_resolvable = tier
            strongest_score = score.score if score is not None else None
            if score is not None and score.score >= floor:
                return tier, True
        if strongest_resolvable is None:
            logger.warning(
                STAKES_ROUTING_TIER_UNRESOLVABLE,
                floor=floor,
                reason="no_tier_resolved",
            )
            return None, False
        logger.warning(
            STAKES_ROUTING_TIER_UNRESOLVABLE,
            floor=floor,
            best_tier=strongest_resolvable,
            best_score=strongest_score,
            reason="no_tier_clears_floor",
        )
        return strongest_resolvable, False

    def _resolve_tier(self, tier: ModelTier) -> ResolvedModel | None:
        """Resolve a tier alias to a model, or ``None``.

        Returns:
            The :class:`ResolvedModel` for ``tier`` when the resolver
            is wired and finds a match; ``None`` otherwise.
        """
        if self._resolver is None:
            return None
        return self._resolver.resolve_safe(tier)

    def _coordination_unhealthy(self, task_id: str) -> bool:
        """True when recent coordination metrics breach a nudge threshold.

        Returns:
            ``True`` when at least one record in the lookback window
            shows error amplification past the threshold; ``False``
            otherwise (or when no store is wired).
        """
        if self._coordination_store is None:
            return False
        records, _ = self._coordination_store.query(
            task_id=task_id,
            limit=self._config.coordination_lookback,
        )
        for rec in records:
            amp = rec.metrics.error_amplification
            if (
                amp is not None
                and amp.value > self._config.error_amplification_threshold
            ):
                return True
            overhead = rec.metrics.overhead
            if (
                overhead is not None
                and overhead.value_percent > self._config.overhead_threshold_percent
            ):
                return True
        return False
