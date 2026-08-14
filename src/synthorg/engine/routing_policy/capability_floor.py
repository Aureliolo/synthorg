# module-kind: code
"""What a task needs, and what an agent is.

Two questions decide whether a given agent may run a given task, and each
has exactly one answer here so assignment and dispatch cannot reach
different verdicts about the same pair:

- *what rung does this task's stakes demand*
  (:meth:`CapabilityFloorPolicy.required_for`)
- *what rung is this agent's bound model* (:class:`AgentCapabilityReader`)

The rung an agent runs at is read from the capability registry, with the
roster's own claim standing in only where the registry has nothing to say.
The roster value is written when an agent is matched and never revised, so
an operator override re-grades a model while every roster row still carries
the rung it was matched at. Where the two disagree the registry wins,
because it is the one that was re-graded.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.agent import ModelConfig
from synthorg.core.task_enums import Stakes
from synthorg.core.types import CapabilityLevel, capability_meets
from synthorg.engine.routing_policy.config import StakesCapabilityFloor
from synthorg.providers.routing.models import ResolvedModel


@runtime_checkable
class AgentCapabilityReader(Protocol):
    """Reads the rung an agent's bound pair actually runs at."""

    def capability_for(self, model: ModelConfig) -> CapabilityLevel | None:
        """Return the pair's rung, or ``None`` when nothing grades it."""
        ...


@runtime_checkable
class PairResolver(Protocol):
    """Resolves a ``(provider, model)`` pair against the model catalogue."""

    def resolve_for_pair(self, provider_name: str, ref: str) -> ResolvedModel | None:
        """Return the catalogue entry for the pair, or ``None``."""
        ...


def clears_floor(
    capability: CapabilityLevel | None,
    required: CapabilityLevel | None,
) -> bool:
    """Report whether *capability* satisfies *required*.

    ``required=None`` means no requirement at all (flat routing, or no floor
    policy wired), so everything clears it.

    An unknown *capability* clears nothing. It means the pair is in no
    configured provider's catalogue and carries no roster rung either, which
    is a binding the dispatch cannot resolve in the first place; refusing it
    here names the problem where it can be reported rather than leaving it to
    surface as a driver lookup failure mid-run.

    Returns:
        ``True`` when the agent may run work at this floor.
    """
    if required is None:
        return True
    if capability is None:
        return False
    return capability_meets(capability, required)


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

    def capability_for(self, model: ModelConfig) -> CapabilityLevel | None:
        """Return the rung *model* runs at.

        Returns:
            The registry's rung for the pair; the roster's own rung when the
            registry does not serve that pair or has not graded it; ``None``
            when neither knows.
        """
        resolved = self._resolver.resolve_for_pair(model.provider, model.model_id)
        if resolved is not None and resolved.capability is not None:
            return resolved.capability
        return model.capability


class CapabilityFloorPolicy:
    """The stakes-to-rung floor, and whether an agent clears it.

    Shared by assignment (which agent may take this task) and by dispatch
    (may this agent run this task now), so a task is never assigned against
    one verdict and then refused against another.

    Args:
        floors: Per-stakes minimum rung.
        reader: Source of an agent's own rung.
    """

    __slots__ = ("_floors", "_reader")

    def __init__(
        self,
        *,
        floors: StakesCapabilityFloor,
        reader: AgentCapabilityReader,
    ) -> None:
        self._floors = floors
        self._reader = reader

    def required_for(self, stakes: Stakes) -> CapabilityLevel:
        """Return the rung *stakes* demands.

        Returns:
            The configured floor for that stakes level.
        """
        return self._floors.for_stakes(stakes)

    def capability_of(self, model: ModelConfig) -> CapabilityLevel | None:
        """Return the rung *model* runs at.

        Returns:
            The reader's answer, or ``None`` when nothing grades the pair.
        """
        return self._reader.capability_for(model)

    def clears(
        self,
        model: ModelConfig,
        required: CapabilityLevel | None,
    ) -> bool:
        """Report whether *model* satisfies *required*.

        Returns:
            ``True`` when the pair's rung meets the floor.
        """
        return clears_floor(self.capability_of(model), required)


__all__ = [
    "AgentCapabilityReader",
    "CapabilityFloorPolicy",
    "PairResolver",
    "ResolvedAgentCapabilityReader",
    "clears_floor",
]
