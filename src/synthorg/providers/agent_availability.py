# module-kind: code
"""An agent whose bound pair cannot serve is an employee who is out.

That is a state an organisation already knows how to handle, and it is far
more explainable than the alternative the loop used to reach for: quietly
running the turn on a different model under the same agent's name.

Availability is DERIVED, never stored. It is a read of the pair's recent
serviceability window, so it reverses itself the moment the window recovers
and nothing has to remember to un-set a flag. The one outcome that does not
decay is an empty balance: a 402 stands until an operator acts, which the
serviceability window already models as a latching failure.
"""

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, computed_field

from synthorg.core.agent import ModelConfig
from synthorg.core.types import NotBlankStr
from synthorg.providers.health import ProviderHealthStatus, ProviderOutcomeClass
from synthorg.providers.serviceability import (
    LATCHING_OUTCOMES,
    ModelServiceability,
    ServiceabilityThresholds,
)
from synthorg.providers.serviceability_settings import (
    resolve_serviceability_thresholds,
)
from synthorg.settings.resolver import ConfigResolver

#: Verdicts that take an agent out of the working roster. DEGRADED does not:
#: a pair answering most calls slowly is still doing the work, and removing
#: every agent on it would turn a slowdown into an outage.
_UNAVAILABLE_VERDICTS = frozenset({ProviderHealthStatus.DOWN})


class AgentUnavailability(BaseModel):
    """Why an agent is out, in the terms an operator can act on.

    Attributes:
        provider_name: Connection the agent's model is reached through.
        model: The model it is bound to.
        verdict: The pair's recent-window verdict.
        outcome_class: The failure that decided it, when one class is
            responsible on its own (an empty balance); ``None`` when the
            verdict came from a rate across mixed failures.
        since: Oldest failing call in the window, so the reason carries how
            long this has been running rather than only that it is.
        needs_operator: Whether the failure is one no retry clears, so the
            agent stays out until somebody acts rather than until the
            window rolls forward.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider_name: NotBlankStr = Field(description="Connection the model is on")
    model: NotBlankStr = Field(description="Model the agent is bound to")
    verdict: ProviderHealthStatus = Field(description="Recent-window verdict")
    outcome_class: ProviderOutcomeClass | None = Field(
        default=None,
        description="Failure class responsible, when one is",
    )
    since: AwareDatetime | None = Field(
        default=None,
        description="Oldest failing call in the window",
    )
    needs_operator: bool = Field(
        default=False,
        description="Whether the failure will not clear on its own",
    )

    @computed_field(description="Operator-facing explanation")
    @property
    def reason(self) -> str:
        """Say which pair, what happened, and whether it will clear itself.

        Returns:
            A sentence naming the pair and the failure.
        """
        pair = f"{self.provider_name}/{self.model}"
        if self.outcome_class is not None:
            detail = f"{pair} is returning {self.outcome_class.value}"
        else:
            detail = f"{pair} is failing most recent calls"
        if self.needs_operator:
            return f"{detail}; this does not clear without an operator"
        return f"{detail}; the agent returns when the pair recovers"


def unavailability_by_pair(
    views: Mapping[tuple[str, str | None], ModelServiceability],
) -> Mapping[tuple[str, str], AgentUnavailability]:
    """Index every unserviceable pair from one fleet-wide read.

    A roster page asking per agent would re-snapshot the record store once
    per row; asking once and joining costs a single pass however many agents
    share a pair.

    Returns:
        Immutable mapping of ``(provider, model)`` to its reason, holding
        only pairs that cannot serve.
    """
    found: dict[tuple[str, str], AgentUnavailability] = {}
    for (provider_name, model), view in views.items():
        if model is None:
            continue
        reason = unavailability_from(view)
        if reason is not None:
            found[provider_name, model] = reason
    return MappingProxyType(found)


@runtime_checkable
class AgentAvailabilityReader(Protocol):
    """Answers whether an agent's bound pair can serve work right now."""

    async def unavailability_for(
        self,
        model: ModelConfig,
        *,
        now: datetime | None = None,
    ) -> AgentUnavailability | None:
        """Return why the pair cannot serve, or ``None`` when it can."""
        ...


@runtime_checkable
class ServiceabilityReader(Protocol):
    """Reads one pair's recent-window serviceability."""

    async def get_serviceability(
        self,
        provider_name: str,
        model: str | None,
        *,
        now: datetime | None = None,
        thresholds: ServiceabilityThresholds | None = None,
    ) -> ModelServiceability:
        """Return the pair's recent-window view."""
        ...


def unavailability_from(view: ModelServiceability) -> AgentUnavailability | None:
    """Derive an agent's unavailability from its pair's recent window.

    Returns:
        The reason the agent is out, or ``None`` when the pair can serve.
        An UNKNOWN verdict is not a reason: a pair nobody has called
        recently has said nothing about itself, and taking its agents out
        on silence would empty a roster the moment it went idle.
    """
    if view.verdict not in _UNAVAILABLE_VERDICTS or view.model is None:
        return None
    latching = [
        outcome for outcome in LATCHING_OUTCOMES if outcome in view.outcome_counts
    ]
    return AgentUnavailability(
        provider_name=view.provider_name,
        model=view.model,
        verdict=view.verdict,
        outcome_class=latching[0] if latching else None,
        since=view.first_failure_timestamp,
        needs_operator=bool(latching),
    )


class ServiceabilityAvailabilityReader:
    """Reads availability from the live serviceability window.

    The verdict boundaries are resolved per read rather than snapshotted at
    boot: they decide which agents are out, so an operator who widens the
    window after an incident should get the roster back on the next pass
    rather than after a restart.

    Args:
        tracker: Source of the recent-window view per pair.
        config_resolver: Live settings read for the boundaries. ``None``
            uses the registered defaults.
    """

    __slots__ = ("_config_resolver", "_tracker")

    def __init__(
        self,
        tracker: ServiceabilityReader,
        *,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        self._tracker = tracker
        self._config_resolver = config_resolver

    async def unavailability_for(
        self,
        model: ModelConfig,
        *,
        now: datetime | None = None,
    ) -> AgentUnavailability | None:
        """Return why *model*'s pair cannot serve, or ``None``.

        Returns:
            The reason the agent bound to this pair is out, or ``None``.
        """
        view = await self._tracker.get_serviceability(
            model.provider,
            model.model_id,
            now=now,
            thresholds=await resolve_serviceability_thresholds(self._config_resolver),
        )
        return unavailability_from(view)


__all__ = [
    "AgentAvailabilityReader",
    "AgentUnavailability",
    "ServiceabilityAvailabilityReader",
    "ServiceabilityReader",
    "unavailability_by_pair",
    "unavailability_from",
]
