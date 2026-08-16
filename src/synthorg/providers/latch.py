# module-kind: code
"""The one call outcome that outlives the window it was measured in.

A latching failure (today only an empty balance) is honoured over a much
longer lookback than the rate window, because a 402 that decayed with the
window would take the pair's agents out of service, stop the calls that are
its own evidence, and read clear one window later. That reasoning lives in
:mod:`synthorg.providers.serviceability`; what lives here is the fact in a
shape that survives a restart.

Every other outcome the tracker holds is deliberately in-memory: it is
high-volume, it decays within minutes, and losing it on restart costs
nothing but a fresh measurement. A latch is the opposite on all three
counts. Its own reason text says *this does not clear without an operator*,
and a process restart is not an operator, so without a durable copy the
lookback stopped being the sole exit and process lifetime became a second,
silent one.

``model`` is required, unlike the record this is built from: a latch is a
fact about a ``(provider, model)`` pair, which is the granularity every
availability read asks at. A real call that named no model still counts
towards the provider's rate window; it just has no pair to latch.
"""

from typing import Final, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.providers.health import (
    ProviderHealthRecord,
    ProviderOutcomeClass,
    RecordSource,
)
from synthorg.providers.serviceability import LATCHING_OUTCOMES

#: Mirrors ``ProviderHealthRecord.error_message``. The two hold the same
#: string, so a cap the record enforces and this does not would reject a
#: rehydrated copy of a row we ourselves wrote.
_ERROR_MESSAGE_MAX_LEN: Final[int] = 1024


class LatchedFailure(BaseModel):
    """One pair's outstanding latching refusal, in durable form.

    Attributes:
        provider_name: Connection the refused call went out on.
        model: Model it named. Required; see the module docstring.
        outcome_class: Which latching failure it was.
        occurred_at: When the pair refused, which is what the lookback is
            measured from and what an operator reads as "since".
        error_message: The redacted provider text, kept so the rehydrated
            record is the one that was recorded rather than a reconstruction
            with the detail dropped.
        response_time_ms: What the refused call took.
        agent_id: Agent the call was attributed to, when one was in scope.
        task_id: Task the call was attributed to, when one was in scope.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider_name: NotBlankStr = Field(description="Connection that refused")
    model: NotBlankStr = Field(description="Model the refused call named")
    outcome_class: ProviderOutcomeClass = Field(description="Which refusal it was")
    occurred_at: AwareDatetime = Field(description="When the pair refused")
    error_message: NotBlankStr = Field(
        max_length=_ERROR_MESSAGE_MAX_LEN,
        description="Redacted provider text for the refusal",
    )
    response_time_ms: float = Field(ge=0.0, description="What the refused call took")
    agent_id: NotBlankStr | None = Field(
        default=None,
        description="Agent attributed with the call, when one was in scope",
    )
    task_id: NotBlankStr | None = Field(
        default=None,
        description="Task attributed with the call, when one was in scope",
    )

    @model_validator(mode="after")
    def _validate_outcome_latches(self) -> Self:
        """Refuse an outcome the reader would never honour as a latch.

        The type's whole claim is that it holds a refusal which outlives its
        measuring window, and ``from_record`` was the only thing asserting it.
        That leaves the claim true only for callers who go through the
        classmethod, and one already does not: rehydrating a row builds this
        directly from a database column, so a hand-edited or corrupted value
        deserialised into a "latch" that is not one. Asserted here instead, so
        the illegal state is unrepresentable by every entry path.

        The field stays typed as the full enum rather than a ``Literal``:
        ``LATCHING_OUTCOMES`` is expected to grow, and a literal would have to
        be edited in lockstep to no benefit.

        Returns:
            ``self`` when the outcome is one the reader latches on.

        Raises:
            ValueError: When it is not.
        """
        if self.outcome_class not in LATCHING_OUTCOMES:
            latching = sorted(o.value for o in LATCHING_OUTCOMES)
            msg = (
                f"{self.outcome_class.value!r} does not latch, so it cannot be "
                f"a LatchedFailure; latching outcomes are {latching}"
            )
            raise ValueError(msg)
        return self

    @classmethod
    def from_record(cls, record: ProviderHealthRecord) -> Self | None:
        """Return the latch *record* establishes, or ``None`` when it is not one.

        The three conditions are exactly the ones
        :func:`~synthorg.providers.serviceability.latched_failure` applies
        when it scans for a latch, plus the pair requirement above. Asking
        them here keeps one answer to "does this outcome latch" rather than
        letting a durable copy be written for something the reader would
        never honour.

        Returns:
            The latch, or ``None`` when the outcome does not establish one.
        """
        if (
            record.source is not RecordSource.REAL_CALL
            or record.outcome_class not in LATCHING_OUTCOMES
            or record.model is None
            or record.error_message is None
        ):
            return None
        return cls(
            provider_name=record.provider_name,
            model=record.model,
            outcome_class=record.outcome_class,
            occurred_at=record.timestamp,
            error_message=record.error_message,
            response_time_ms=record.response_time_ms,
            agent_id=record.agent_id,
            task_id=record.task_id,
        )

    def to_record(self) -> ProviderHealthRecord:
        """Rebuild the outcome the tracker reads latches from.

        Rehydration goes back through the record list rather than into a
        second latch-shaped store the reader would also have to consult:
        the verdict stays derived from one sequence of outcomes, so nothing
        new decides whether a pair can serve.

        Returns:
            The refused call as the tracker recorded it.
        """
        return ProviderHealthRecord(
            provider_name=self.provider_name,
            model=self.model,
            timestamp=self.occurred_at,
            success=False,
            outcome_class=self.outcome_class,
            response_time_ms=self.response_time_ms,
            error_message=self.error_message,
            source=RecordSource.REAL_CALL,
            agent_id=self.agent_id,
            task_id=self.task_id,
        )

    @property
    def pair(self) -> tuple[str, str]:
        """The ``(provider, model)`` key this latch is stored under."""
        return str(self.provider_name), str(self.model)


__all__ = ["LatchedFailure"]
