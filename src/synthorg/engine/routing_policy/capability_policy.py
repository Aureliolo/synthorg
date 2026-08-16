# module-kind: code
"""What a piece of work needs, and whether a given agent may take it.

One object answers every capability question the loop asks, so selection and
dispatch cannot reach different verdicts about the same pair:

- *what rung does this work demand* (:meth:`CapabilityPolicy.required_for`,
  the stakes floor raised one rung by substantial complexity)
- *what rung is this agent's bound model* (:class:`AgentCapabilityReader`)
- *may this agent take it, and how well does it fit*
  (:meth:`CapabilityPolicy.judge`)
- *how hard should the model think, and does the deliverable need a red team*

The judgement never rewrites what an agent runs. An agent is a fixed
``(role, personality, model)`` unit, so work needing more capability goes to a
DIFFERENT agent; the ladder prefers an exact match, then the nearest rung
above, then the nearest rung below with the concession logged. Preferring an
exact match over a stronger one is also the org's standing cost discipline,
applied on every assignment rather than only once a budget threshold is
crossed. It buys the cheapest agent AT the rung the work demands, not the
cheapest that could scrape through: the band is chosen first and cost orders
the candidates within it.

Going lower is a last resort rather than a refusal for low and normal stakes,
because a weaker agent still does the work and every deliverable still passes
the completion oracle. Above the configured stakes floor it IS a refusal: the
inner-loop A/B recording measured complex and epic briefs failing the
correctness gate outright on a basic model rather than degrading, so parking
for an operator decision is the honest answer there.

The rung an agent runs at is read from the capability registry, with the
roster's own claim standing in only where the registry has nothing to say. The
roster value is written when an agent is matched and never revised, so an
operator override re-grades a model while every roster row still carries the
rung it was matched at. Where the two disagree the registry wins, because it
is the one that was re-graded.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.agent import ModelConfig
from synthorg.core.capability_fit import CapabilityFit
from synthorg.core.completion_enums import ReasoningEffort
from synthorg.core.task_enums import Complexity, Stakes, compare_stakes
from synthorg.core.types import CapabilityLevel, capability_meets, capability_rank
from synthorg.engine.routing_policy.capability_ladder import (
    SUBSTANTIAL_COMPLEXITIES,
    bump_one,
)
from synthorg.engine.routing_policy.config import CapabilityPolicyConfig
from synthorg.providers.routing.models import ResolvedModel


@runtime_checkable
class AgentCapabilityReader(Protocol):
    """Reads the rung an agent's bound pair actually runs at.

    Keyed on the pair rather than on a :class:`ModelConfig` so the roster's
    stored form is not the only shape that can ask. The API projects agents
    from raw config dicts, and forcing those through a typed model to ask this
    question would mean minting a binding to read one.
    """

    def capability_for_pair(
        self,
        provider: str,
        model_id: str,
        *,
        claimed: CapabilityLevel | None,
    ) -> CapabilityLevel | None:
        """Return the pair's rung, or ``None`` when nothing grades it."""
        ...


@runtime_checkable
class PairResolver(Protocol):
    """Resolves a ``(provider, model)`` pair against the model catalogue."""

    def resolve_for_pair(self, provider_name: str, ref: str) -> ResolvedModel | None:
        """Return the catalogue entry for the pair, or ``None``."""
        ...


class CapabilityVerdict(BaseModel):
    """How one agent's bound model measures against one piece of work.

    Attributes:
        required: The rung the work demands.
        agent: The rung the agent's bound pair runs at, or ``None`` when
            nothing grades it.
        fit: How the agent compares: an exact ``match``, a rung ``higher``,
            or a rung ``lower``.
        sanctioned: Whether this agent may take the work. ``False`` only for
            a ``lower`` fit at or above the configured park floor, and for an
            ungraded pair, which is a binding the dispatch cannot resolve at
            all rather than a weak one.
        unresolved: Whether the pair carries no rung at all. Refusals for the
            two reasons need different things from the operator, and a run
            that reports only "below capability" sends them looking for a
            stronger model when the pair simply has no grade to compare.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    required: CapabilityLevel = Field(description="Rung the work demands")
    agent: CapabilityLevel | None = Field(
        default=None,
        description="Rung the agent's bound pair runs at",
    )
    fit: CapabilityFit = Field(description="How the agent compares to the requirement")
    sanctioned: bool = Field(description="Whether this agent may take the work")
    unresolved: bool = Field(
        default=False,
        description="Whether nothing grades the agent's bound pair",
    )


def described_capability(
    policy: CapabilityPolicy | None,
    model: ModelConfig,
) -> CapabilityLevel | None:
    """Return the rung to describe *model* by, outside a gating decision.

    What an agent is told about its own tier, and what an operator reads on a
    dashboard, has to be the rung the gates will actually judge it at. Reading
    the roster claim instead leaves both describing a model the catalogue has
    since re-graded, which is only ever noticed as an argument about why the
    work was refused.

    Args:
        policy: The one capability policy, or ``None`` before it is wired.
        model: The bound pair to describe.

    Returns:
        The catalogue's rung, or the roster's claim when no policy is wired
        to consult one.
    """
    return described_pair_capability(
        policy,
        provider=model.provider,
        model_id=model.model_id,
        claimed=model.capability,
    )


def described_pair_capability(
    policy: CapabilityPolicy | None,
    *,
    provider: str,
    model_id: str,
    claimed: CapabilityLevel | None,
) -> CapabilityLevel | None:
    """Return the rung to describe a ``(provider, model)`` pair by.

    The pair-shaped entry point to :func:`described_capability`, for a caller
    holding the binding in its stored form rather than as a typed model.

    Args:
        policy: The one capability policy, or ``None`` before it is wired.
        provider: Registered connection the pair is reached through.
        model_id: Model the connection serves.
        claimed: The roster's own rung for this pair, if it carries one.

    Returns:
        The catalogue's rung, or *claimed* when no policy is wired to consult
        one. ``None`` for a pair that names nothing, whatever it claims.
    """
    if not provider.strip() or not model_id.strip():
        # A rung describes what a binding can do, so a blank binding describes
        # nothing. Returning the claim here would let an agent bound to no pair
        # read as whatever rung its roster row happened to carry, which is the
        # unbacked claim the capability work exists to remove.
        return None
    if policy is None:
        return claimed
    return policy.capability_of_pair(provider, model_id, claimed=claimed)


class ResolvedAgentCapabilityReader:
    """Reads an agent's rung from the capability registry.

    Args:
        resolver: The model catalogue, carrying each pair's effective rung
            (the heuristic classification overlaid by published evidence and
            by operator / LLM overrides).
    """

    __slots__ = ("_resolver",)

    def __init__(self, resolver: PairResolver) -> None:
        self._resolver = resolver

    def capability_for_pair(
        self,
        provider: str,
        model_id: str,
        *,
        claimed: CapabilityLevel | None,
    ) -> CapabilityLevel | None:
        """Return the rung the pair runs at.

        Args:
            provider: Registered connection the pair is reached through.
            model_id: Model the connection serves.
            claimed: The roster's own rung for this pair, if it carries one.

        Returns:
            The registry's rung for the pair; *claimed* when the registry does
            not serve that pair or has not graded it; ``None`` when neither
            knows.
        """
        resolved = self._resolver.resolve_for_pair(provider, model_id)
        if resolved is not None and resolved.capability is not None:
            return resolved.capability
        return claimed


class CapabilityPolicy:
    """The single owner of every capability question the loop asks.

    One instance is built at boot and shared by selection (which agents may
    be offered the work) and dispatch (may THIS agent run it now), so the two
    cannot disagree. :meth:`set_config` re-points the whole graph at a
    freshly resolved configuration, which is what makes the operator's
    settings live without rewiring five consumers.

    Args:
        config: Per-stakes floors, reasoning depths, and the two stakes
            thresholds (red team, park-rather-than-lower).
        reader: Source of an agent's own rung.
    """

    __slots__ = ("_config", "_reader")

    def __init__(
        self,
        *,
        config: CapabilityPolicyConfig,
        reader: AgentCapabilityReader,
    ) -> None:
        self._config = config
        self._reader = reader

    def set_config(self, config: CapabilityPolicyConfig) -> None:
        """Adopt a re-resolved configuration for subsequent judgements.

        Args:
            config: The freshly resolved configuration.
        """
        self._config = config

    @property
    def config(self) -> CapabilityPolicyConfig:
        """The configuration currently in force."""
        return self._config

    def required_for(
        self,
        stakes: Stakes,
        complexity: Complexity = Complexity.MEDIUM,
    ) -> CapabilityLevel:
        """Return the rung this work demands of whoever handles it.

        Read off the WORK, never off the handler's seniority: the stakes set
        the floor and substantial complexity raises it one rung.

        Args:
            stakes: How consequential the work is.
            complexity: The work's estimated complexity. Defaults to MEDIUM,
                which is judged at the bare stakes floor.

        Returns:
            The rung the work demands.
        """
        floor = self._config.capability_floors.for_stakes(stakes)
        if complexity in SUBSTANTIAL_COMPLEXITIES:
            return bump_one(floor)
        return floor

    def capability_of(self, model: ModelConfig) -> CapabilityLevel | None:
        """Return the rung *model* runs at.

        Returns:
            The reader's answer, or ``None`` when nothing grades the pair.
        """
        return self.capability_of_pair(
            model.provider, model.model_id, claimed=model.capability
        )

    def capability_of_pair(
        self,
        provider: str,
        model_id: str,
        *,
        claimed: CapabilityLevel | None,
    ) -> CapabilityLevel | None:
        """Return the rung a ``(provider, model)`` pair runs at.

        Args:
            provider: Registered connection the pair is reached through.
            model_id: Model the connection serves.
            claimed: The roster's own rung for this pair, if it carries one.

        Returns:
            The reader's answer, or ``None`` when nothing grades the pair.
        """
        return self._reader.capability_for_pair(provider, model_id, claimed=claimed)

    def judge(
        self,
        *,
        model: ModelConfig,
        stakes: Stakes,
        complexity: Complexity = Complexity.MEDIUM,
    ) -> CapabilityVerdict:
        """Measure *model* against what this work demands.

        Returns:
            The :class:`CapabilityVerdict`, whose ``sanctioned`` flag is the
            single answer to "may this agent take it" that both selection and
            dispatch read.
        """
        required = self.required_for(stakes, complexity)
        agent = self.capability_of(model)
        if agent is None:
            # A pair in no configured catalogue and carrying no roster rung is
            # a binding the dispatch cannot resolve, not a weak one; refusing
            # here names the problem where it can be reported rather than
            # leaving it to surface as a driver lookup failure mid-run.
            return CapabilityVerdict(
                required=required,
                agent=None,
                fit="lower",
                sanctioned=False,
                unresolved=True,
            )
        fit = self._fit(agent, required)
        return CapabilityVerdict(
            required=required,
            agent=agent,
            fit=fit,
            sanctioned=fit != "lower" or not self.parks_when_lower(stakes),
        )

    def parks_when_lower(self, stakes: Stakes) -> bool:
        """Report whether work at *stakes* refuses a weaker agent outright.

        Returns:
            ``True`` at or above the configured park floor.
        """
        return compare_stakes(stakes, self._config.park_min_stakes) >= 0

    def reasoning_effort(self, stakes: Stakes) -> ReasoningEffort | None:
        """Return the reasoning depth to request for work at *stakes*.

        This is the one dial stakes turn on the call itself, because it tunes
        how the bound model works rather than which model runs.

        Returns:
            The configured effort, or ``None`` for the provider default.
        """
        return self._config.reasoning.for_stakes(stakes)

    def red_team_required(self, stakes: Stakes) -> bool:
        """Report whether a deliverable at *stakes* must pass the red team.

        Returns:
            ``True`` at or above the configured red-team floor.
        """
        return compare_stakes(stakes, self._config.red_team_min_stakes) >= 0

    @staticmethod
    def _fit(agent: CapabilityLevel, required: CapabilityLevel) -> CapabilityFit:
        """Classify *agent* against *required*.

        Returns:
            ``"match"``, ``"higher"`` or ``"lower"``.
        """
        if agent == required:
            return "match"
        return "higher" if capability_meets(agent, required) else "lower"


def rank_of(capability: CapabilityLevel | None) -> int:
    """Return a sortable rank, with an ungraded pair below every rung.

    Returns:
        The capability's rank, or ``-1`` when nothing grades the pair.
    """
    return -1 if capability is None else capability_rank(capability)


__all__ = [
    "AgentCapabilityReader",
    "CapabilityFit",
    "CapabilityPolicy",
    "CapabilityVerdict",
    "PairResolver",
    "ResolvedAgentCapabilityReader",
    "described_capability",
    "described_pair_capability",
    "rank_of",
]
