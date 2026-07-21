"""Stakes-aware and flat routing strategies."""

from synthorg.budget.coordination_store import CoordinationMetricsStore
from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.core.task_enums import Stakes, compare_stakes
from synthorg.core.types import ModelTier
from synthorg.engine.routing_policy.config import StakesRoutingConfig
from synthorg.engine.routing_policy.errors import StakesModelUnavailableError
from synthorg.engine.routing_policy.models import StakesRoutingDecision
from synthorg.engine.routing_policy.tiers import bump_one, higher_tier
from synthorg.observability import get_logger
from synthorg.observability.events.stakes_routing import (
    STAKES_ROUTING_COORD_NUDGE,
    STAKES_ROUTING_ESCALATED,
    STAKES_ROUTING_TIER_ADJUSTED,
)
from synthorg.providers.routing.models import ResolvedModel
from synthorg.providers.routing.resolver import ModelResolver

logger = get_logger(__name__)


class FlatStrategy:
    """No-op routing: keeps the agent's configured model, no red-team.

    This is the control arm of the cost/quality comparison test and the
    opt-out selectable via the ``flat`` discriminator.
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
    """Route by stakes to a required model tier, escalating when unmet.

    Picks the cheapest configured, tool-capable model whose assigned tier
    meets the per-stakes tier requirement, bumps the requirement one tier when
    recent coordination metrics look unhealthy, and never routes red-team-gated
    work (stakes at or above ``config.red_team_min_stakes``) below the agent's
    configured tier. When no configured tool-capable model meets the
    requirement it raises :class:`StakesModelUnavailableError` so the engine
    escalates or fails loudly: consequential work is never silently run on a
    sub-tier model.

    Selection gates on the resolved model's tier and tool-capability only. Each
    model's classification ``confidence`` is operator-facing (surfaced in the
    tier-assignment panel for review) and is deliberately not consulted here, so
    an unenriched model admitted by the optimistic capability default can still
    satisfy a tier requirement; the operator lowers a wrong tier via an override.

    Deterministic given the resolver's catalogue and coordination records;
    performs no wall-clock reads or live provider calls.

    Args:
        resolver: Resolves the required tier to the cheapest tool-capable
            configured model. Required: the strategy cannot gate on tier or
            capability without a catalogue.
        config: Per-stakes tier requirements, nudge thresholds, and the
            red-team threshold.
        coordination_store: Recent coordination metrics for the nudge. When
            ``None``, the nudge is skipped.
    """

    def __init__(
        self,
        *,
        resolver: ModelResolver,
        config: StakesRoutingConfig | None = None,
        coordination_store: CoordinationMetricsStore | None = None,
    ) -> None:
        self._resolver = resolver
        self._config = config or StakesRoutingConfig()
        self._coordination_store = coordination_store

    async def route(
        self,
        *,
        task: Task,
        identity: AgentIdentity,
    ) -> StakesRoutingDecision:
        """Route *task* to a model meeting its stakes tier requirement.

        Returns:
            The :class:`StakesRoutingDecision` with the selected model, the
            red-team requirement, stakes, reason, and source label.

        Raises:
            StakesModelUnavailableError: When no configured tool-capable model
                meets the required tier.
        """
        stakes = task.stakes
        red_team_required = (
            compare_stakes(stakes, self._config.red_team_min_stakes) >= 0
        )
        required, nudged = self._adjusted_required_tier(
            task=task,
            identity=identity,
            stakes=stakes,
            red_team_required=red_team_required,
        )

        selected = self._select_model(required)
        if selected is None:
            logger.warning(
                STAKES_ROUTING_ESCALATED,
                task_id=str(task.id),
                agent_id=str(identity.id),
                stakes=stakes.value,
                required_tier=required,
                reason="no_tool_capable_model_at_tier",
            )
            raise StakesModelUnavailableError(
                stakes=stakes,
                required_tier=required,
            )

        return self._build_decision(
            identity=identity,
            stakes=stakes,
            red_team_required=red_team_required,
            required_tier=required,
            selected=selected,
            nudged=nudged,
        )

    def _adjusted_required_tier(
        self,
        *,
        task: Task,
        identity: AgentIdentity,
        stakes: Stakes,
        red_team_required: bool,
    ) -> tuple[ModelTier, bool]:
        """Base stakes tier adjusted for coordination health + red-team floor.

        Returns:
            The (possibly bumped then floored) required tier, and whether a
            coordination nudge fired.
        """
        required = self._config.stakes_tiers.for_stakes(stakes)
        nudged = False
        if self._coordination_unhealthy(str(task.id)):
            bumped = bump_one(required)
            if bumped != required:
                logger.info(
                    STAKES_ROUTING_COORD_NUDGE,
                    task_id=str(task.id),
                    from_tier=required,
                    to_tier=bumped,
                )
                nudged = True
            required = bumped

        # Red-team-gated work must never run below the agent's configured tier.
        current_tier = identity.model.model_tier
        if red_team_required and current_tier is not None:
            floored = higher_tier(required, current_tier)
            if floored != required:
                logger.info(
                    STAKES_ROUTING_TIER_ADJUSTED,
                    task_id=str(task.id),
                    from_tier=required,
                    to_tier=floored,
                    reason="red_team_floor",
                )
            required = floored
        return required, nudged

    def _select_model(self, required: ModelTier) -> ResolvedModel | None:
        """Return the cheapest tool-capable model at or above *required*.

        Returns:
            The cheapest resolved model whose tier meets ``required`` and which
            can execute tool-bearing work, or ``None`` when none qualifies.
        """
        for candidate in self._resolver.models_at_or_above_tier(required):
            if candidate.tool_capable:
                return candidate
        return None

    def _build_decision(  # noqa: PLR0913 -- keyword-only assembly inputs
        self,
        *,
        identity: AgentIdentity,
        stakes: Stakes,
        red_team_required: bool,
        required_tier: ModelTier,
        selected: ResolvedModel,
        nudged: bool,
    ) -> StakesRoutingDecision:
        """Assemble the decision from the selected model.

        Returns:
            A :class:`StakesRoutingDecision` whose ``selected_model`` is the
            routed model (when it differs from the agent's) or the agent's
            current model (when it already satisfies the requirement).
        """
        current = identity.model
        changed = (
            selected.model_id != current.model_id
            or selected.provider_name != current.provider
            or selected.tier != current.model_tier
        )
        if changed:
            selected_model = current.model_copy(
                update={
                    "provider": selected.provider_name,
                    "model_id": selected.model_id,
                    "model_tier": selected.tier,
                },
            )
            source = "stakes_aware:nudge" if nudged else "stakes_aware:routed"
            reason = (
                f"stakes={stakes.value}: routed to {selected.tier} tier model "
                f"{selected.model_id} (>= required {required_tier})"
            )
        else:
            selected_model = current
            source = "stakes_aware:kept"
            reason = (
                f"stakes={stakes.value}: kept "
                f"{current.model_tier or 'configured'} tier "
                f"(meets required {required_tier})"
            )

        return StakesRoutingDecision(
            selected_model=selected_model,
            red_team_required=red_team_required,
            stakes=stakes,
            reasoning_effort=self._config.stakes_reasoning.for_stakes(stakes),
            reason=reason,
            source=source,
        )

    def _coordination_unhealthy(self, task_id: str) -> bool:
        """True when recent coordination metrics breach a nudge threshold.

        Returns:
            ``True`` when at least one record in the lookback window shows
            error amplification or overhead past the threshold; ``False``
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
