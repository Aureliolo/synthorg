"""Stakes-aware and flat routing strategies."""

from synthorg.budget.coordination_store import CoordinationMetricsStore
from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.core.task_enums import Stakes, compare_stakes
from synthorg.core.types import CapabilityLevel
from synthorg.engine.routing_policy.config import StakesRoutingConfig
from synthorg.engine.routing_policy.errors import StakesModelUnavailableError
from synthorg.engine.routing_policy.models import StakesRoutingDecision
from synthorg.engine.routing_policy.tiers import (
    bump_one,
    higher_tier,
    meets_required,
)
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

    The agent's own model is the decision unless it is too weak. An operator
    chose that ``(provider, model)`` pair for that role, so stakes routing may
    only raise it: if the agent's model meets the tier the stakes demand, it
    keeps it; below the requirement it routes up, to the cheapest tool-capable
    model that qualifies. There is no cheapest-within-tier step over an
    already-adequate agent, because with every model in a tier priced the same
    (a local gateway prices everything zero) that is an arbitrary tie whose
    winner takes every task in the org, which is how one incompatible model
    took down five agents at once.

    The requirement itself is bumped one tier when recent coordination metrics
    look unhealthy, and floored at the agent's own tier for red-team-gated work
    (stakes at or above ``config.red_team_min_stakes``). When nothing qualifies
    it raises :class:`StakesModelUnavailableError` so the engine escalates or
    fails loudly: consequential work is never silently run on a sub-tier model.

    A model's tier comes from the resolver whenever the resolver has an answer.
    The roster's ``model_tier`` is written when an agent is matched and goes
    stale the moment an operator overrides a tier, so the two disagreed
    (``medium`` on the roster, ``large`` in the tier registry) and the
    disagreement decided routing. The resolver reads the effective tier map, so
    it is the authority; a stale roster value is corrected onto the returned
    model rather than consulted.

    The one place the roster tier is read is the red-team floor, and only when
    the resolver misses. A floor that vanishes because a model could not be
    resolved is a floor that stops applying exactly when the routing is least
    certain, so the roster's own claim about the agent stands in: it may be
    stale, but it is the agent's declared tier and routing is never floored
    below it.

    Selection gates on the resolved model's tier and tool-capability only. Each
    model's classification ``confidence`` is operator-facing (surfaced in the
    tier-assignment panel for review) and is deliberately not consulted here, so
    an unenriched model admitted by the optimistic capability default can still
    satisfy a tier requirement; the operator lowers a wrong tier via an override.

    Deterministic given the resolver's catalogue and coordination records;
    performs no wall-clock reads or live provider calls.

    Args:
        resolver: Resolves the agent's own pair and the required tier to a
            tool-capable configured model. Required: the strategy cannot gate
            on tier or capability without a catalogue.
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
        current = self._resolver.resolve_for_pair(
            identity.model.provider,
            identity.model.model_id,
        )
        required, nudged = self._adjusted_required_tier(
            task=task,
            current_tier=current.capability if current is not None else None,
            roster_tier=identity.model.capability,
            stakes=stakes,
            red_team_required=red_team_required,
        )

        selected = self._select_model(required, current=current)
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
        current_tier: CapabilityLevel | None,
        roster_tier: CapabilityLevel | None,
        stakes: Stakes,
        red_team_required: bool,
    ) -> tuple[CapabilityLevel, bool]:
        """Base stakes tier adjusted for coordination health + red-team floor.

        *current_tier* is the agent's model's tier as the resolver reports it,
        which outranks the roster wherever it has an answer, so a stale roster
        value cannot lower the red-team floor below what the agent actually
        runs. *roster_tier* is the floor's fallback for the case the resolver
        has no answer at all (a pair absent from the catalogue): a stale tier
        is still a floor, and no floor would let red-team-gated work route
        below the agent's own configured tier.

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
        floor_tier = current_tier if current_tier is not None else roster_tier
        if red_team_required and floor_tier is not None:
            floored = higher_tier(required, floor_tier)
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

    def _select_model(
        self,
        required: CapabilityLevel,
        *,
        current: ResolvedModel | None,
    ) -> ResolvedModel | None:
        """Return the model this task should run on.

        The agent's own model wins whenever it is adequate. Routing up is for
        the case the operator's choice cannot carry the stakes, and it is the
        only case: an agent already at or above the requirement is left alone
        even when a cheaper qualifying model exists, because "cheapest in tier"
        over an adequate agent is an arbitrary pick that concentrates the whole
        org onto one model.

        Args:
            required: The tier the stakes demand, already bumped and floored.
            current: The agent's own model as the resolver reports it, or
                ``None`` when its bound pair is not in the catalogue.

        Returns:
            The agent's own model when it qualifies; otherwise the cheapest
            resolved model whose tier meets ``required`` and which can execute
            tool-bearing work; ``None`` when nothing qualifies.
        """
        if (
            current is not None
            and current.tool_capable
            and current.capability is not None
            and meets_required(current.capability, required)
        ):
            return current
        for candidate in self._resolver.models_at_or_above_tier(required):
            if candidate.tool_capable:
                return candidate
        return None

    def _build_decision(
        self,
        *,
        identity: AgentIdentity,
        stakes: Stakes,
        red_team_required: bool,
        required_tier: CapabilityLevel,
        selected: ResolvedModel,
        nudged: bool,
    ) -> StakesRoutingDecision:
        """Assemble the decision from the selected model.

        A tier that differs while the model does not is the roster disagreeing
        with the tier registry, not a route: the resolver's value is written
        onto the returned model so downstream prompt-profile selection reads
        the tier the model actually has, and the decision still reports
        ``kept``.

        Returns:
            A :class:`StakesRoutingDecision` whose ``selected_model`` is the
            routed model (when it differs from the agent's) or the agent's
            current model (when it already satisfies the requirement).
        """
        current = identity.model
        routed = (
            selected.model_id != current.model_id
            or selected.provider_name != current.provider
        )
        if routed:
            selected_model = current.model_copy(
                update={
                    "provider": selected.provider_name,
                    "model_id": selected.model_id,
                    "capability": selected.capability,
                },
            )
            source = "stakes_aware:nudge" if nudged else "stakes_aware:routed"
            reason = (
                f"stakes={stakes.value}: routed to {selected.capability} tier model "
                f"{selected.model_id} (>= required {required_tier})"
            )
        else:
            if selected.capability != current.capability:
                logger.info(
                    STAKES_ROUTING_TIER_ADJUSTED,
                    agent_id=str(identity.id),
                    model_id=current.model_id,
                    from_tier=current.model_tier,
                    to_tier=selected.capability,
                    reason="roster_tier_stale",
                )
                current = current.model_copy(update={"capability": selected.capability})
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
