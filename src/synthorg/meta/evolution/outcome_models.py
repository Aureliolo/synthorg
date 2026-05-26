"""Models for the evolution outcome store.

The store records :class:`EvolutionOutcomeRecord` instances; the
aggregator derives a window summary from them.  Records are frozen so
they can be safely returned from the store to callers without copy-on-
read ceremony.
"""

from datetime import UTC, datetime
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.meta.models import ProposalAltitude  # noqa: TC001


class EvolutionOutcomeRecord(BaseModel):
    """Terminal record of a proposal that reached a decision.

    Attributes:
        agent_id: Which agent was the target of the proposal.
        axis: Which altitude / axis the proposal targeted (matches
            :class:`~synthorg.meta.models.ProposalAltitude` values).
        applied: ``True`` when the proposal was applied and did not
            roll back; ``False`` for rejected / rolled-back / failed.
        proposed_at: When the proposal was generated.
        recorded_at: When the terminal outcome was recorded. Must be
            greater than or equal to ``proposed_at`` -- an outcome can
            only be recorded at or after the proposal was made.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr
    axis: ProposalAltitude
    applied: bool
    proposed_at: AwareDatetime
    recorded_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )

    @model_validator(mode="after")
    def _recorded_at_not_before_proposed(self) -> Self:
        """Return recorded at not before proposed.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if self.recorded_at < self.proposed_at:
            msg = (
                "recorded_at must be greater than or equal to proposed_at; "
                f"got recorded_at={self.recorded_at.isoformat()} "
                f"proposed_at={self.proposed_at.isoformat()}"
            )
            raise ValueError(msg)
        return self
