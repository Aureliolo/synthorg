# module-kind: code
"""How one agent's own calls actually went, on the pair it is bound to.

This comparison is only valid because an agent is a fixed
``(role, personality, model)`` unit: while the loop could re-dispatch a
turn onto different horsepower under the same name, "how did this agent
perform" had no answer, because the runs were spread across whatever the
stakes ladder reached for.

Two things keep it honest:

- **Probe traffic is excluded.** A reachability probe belongs to no agent
  and answers a different question; letting a healthy probe cadence dilute
  a failing agent's numbers is the exact shape of the reporting defect that
  motivated the serviceability window.
- **Every cell carries its sample size**, and one below the operator's
  floor reports as insufficient rather than as a number. A rate over four
  calls is not a measurement, and rendering it beside one over four hundred
  invites a decision the data cannot support.

Agent attributes (role, department, the personality axes) are joined here
from the live roster and never written onto a record: a row that copied an
agent's department would silently change meaning the day that agent moved,
which is what makes historical numbers wrong.
"""

from collections.abc import Mapping, Sequence
from typing import Final

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, computed_field

from synthorg.core.agent import AgentIdentity
from synthorg.core.types import CapabilityLevel, NotBlankStr
from synthorg.hr.enums import CreativityLevel, DecisionMakingStyle, RiskTolerance
from synthorg.providers.health import (
    ProviderHealthRecord,
    ProviderOutcomeClass,
    RecordSource,
)
from synthorg.providers.serviceability import LatencyDistribution, percentile

#: Calls before a profile reports rates rather than "not enough yet". Twenty
#: is the point at which a single bad call stops moving the success rate by
#: more than a few percent, which is the smallest sample worth comparing two
#: agents on.
DEFAULT_MIN_CALLS_FOR_PROFILE: Final[int] = 20

_PERCENT: Final[float] = 100.0


class DispatchProfile(BaseModel):
    """One agent's dispatch record over the window, with its sample size.

    Attributes:
        agent_id: Stable runtime identifier.
        agent_name: Display name, so a comparison reads as people.
        role: Role label, joined live from the roster.
        department: Department label, joined live from the roster.
        risk_tolerance: Personality axis, so two agents differing only in
            temperament can be compared side by side.
        decision_making: Personality axis.
        creativity: Personality axis.
        provider_name: Connection the agent is bound to.
        model: Model on that connection.
        capability: The rung the agent's pair is graded at, when graded.
        call_count: Real calls the agent made in the window.
        outcome_counts: Count per outcome class; absent classes are absent
            rather than zero, so "did not happen" reads differently from
            "happened zero times".
        latency: Distribution over the window, or ``None`` when empty.
        last_call_at: Most recent real call.
        min_calls: The floor this profile is judged against.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Stable runtime identifier")
    agent_name: NotBlankStr = Field(description="Display name")
    role: NotBlankStr = Field(description="Role label")
    department: NotBlankStr = Field(description="Department label")
    risk_tolerance: RiskTolerance = Field(description="Personality axis")
    decision_making: DecisionMakingStyle = Field(description="Personality axis")
    creativity: CreativityLevel = Field(description="Personality axis")
    provider_name: NotBlankStr = Field(description="Bound connection")
    model: NotBlankStr = Field(description="Bound model")
    capability: CapabilityLevel | None = Field(
        default=None, description="Graded rung of the bound pair"
    )
    call_count: int = Field(default=0, ge=0, description="Real calls in the window")
    outcome_counts: Mapping[ProviderOutcomeClass, int] = Field(
        default_factory=dict, description="Count per outcome class"
    )
    latency: LatencyDistribution | None = Field(
        default=None, description="Latency distribution"
    )
    last_call_at: AwareDatetime | None = Field(
        default=None, description="Most recent real call"
    )
    min_calls: int = Field(
        default=DEFAULT_MIN_CALLS_FOR_PROFILE,
        ge=1,
        description="Sample floor this profile is judged against",
    )

    @computed_field(description="Whether the sample supports a comparison")
    @property
    def has_enough_calls(self) -> bool:
        """Whether the window holds enough calls to compare on."""
        return self.call_count >= self.min_calls

    @computed_field(description="Share of the agent's calls that succeeded")
    @property
    def success_rate_percent(self) -> float:
        """Succeeded share of the window, or 0.0 when it is empty."""
        if self.call_count == 0:
            return 0.0
        succeeded = self.outcome_counts.get(ProviderOutcomeClass.SUCCESS, 0)
        return round(succeeded / self.call_count * _PERCENT, 2)


def build_dispatch_profile(
    identity: AgentIdentity,
    records: Sequence[ProviderHealthRecord],
    *,
    min_calls: int = DEFAULT_MIN_CALLS_FOR_PROFILE,
    capability: CapabilityLevel | None = None,
) -> DispatchProfile:
    """Summarise one agent's own calls, joined to its live identity.

    Args:
        identity: The agent as the roster has it now.
        records: Outcomes attributed to this agent, in any order.
        min_calls: Sample floor below which the profile reports as
            insufficient rather than as a rate.
        capability: The rung the catalogue grades this pair at, which is the
            rung selection and dispatch will judge it by. The caller resolves
            it, because the catalogue sits above this layer. Falls back to the
            roster's own claim, which is what the catalogue itself falls back
            to for a pair it does not grade.

    Returns:
        The agent's profile over the supplied records.
    """
    real = [record for record in records if record.source is RecordSource.REAL_CALL]
    counts: dict[ProviderOutcomeClass, int] = {}
    for record in real:
        counts[record.outcome_class] = counts.get(record.outcome_class, 0) + 1
    return DispatchProfile(
        agent_id=NotBlankStr(str(identity.id)),
        agent_name=NotBlankStr(str(identity.name)),
        role=NotBlankStr(str(identity.role)),
        department=NotBlankStr(str(identity.department)),
        risk_tolerance=identity.personality.risk_tolerance,
        decision_making=identity.personality.decision_making,
        creativity=identity.personality.creativity,
        provider_name=NotBlankStr(str(identity.model.provider)),
        model=NotBlankStr(str(identity.model.model_id)),
        capability=capability if capability is not None else identity.model.capability,
        call_count=len(real),
        outcome_counts=counts,
        latency=_distribution([r.response_time_ms for r in real]),
        last_call_at=max((r.timestamp for r in real), default=None),
        min_calls=min_calls,
    )


def _distribution(latencies: list[float]) -> LatencyDistribution | None:
    """Build the latency distribution for one agent's window.

    Returns:
        The distribution, or ``None`` when the agent made no calls.
    """
    if not latencies:
        return None
    ordered = sorted(latencies)
    return LatencyDistribution(
        p50_ms=percentile(ordered, 0.50),
        p90_ms=percentile(ordered, 0.90),
        p99_ms=percentile(ordered, 0.99),
        max_ms=ordered[-1],
    )


__all__ = [
    "DEFAULT_MIN_CALLS_FOR_PROFILE",
    "DispatchProfile",
    "build_dispatch_profile",
]
