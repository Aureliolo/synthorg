# module-kind: declarative
"""How far a running decomposition has got, as a snapshot on the plan."""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

#: A ledger built with no ceiling reports this limit, which reads as "unknown"
#: rather than as a bound of zero. Only the evals harness builds one, and it
#: never reports; the constant exists so the invariant below can say so.
NO_SESSION_LIMIT: int = 0


class DecompositionProgress(BaseModel):
    """How far the decomposition writing a plan has got.

    A recursive decomposition persists its tree once, at the end, so a plan is
    ``PLANNING`` with zero items for as long as the planning runs. That is
    correct, and it left the operator with nothing: a live run sat at zero for
    54 minutes while the page promised "items appear as they are written", and
    the only way to tell a working decomposition from a hung one was the
    backend log. The session ledger bounding the run knows all of this and
    knew it only in memory.

    A snapshot, not a log: it is overwritten each time the decomposition
    reaches a new node, because the question it answers is "where is this now",
    and the run's own history is the event stream's job.

    Attributes:
        sessions_spent: Planning sessions the tree has consumed so far.
        sessions_limit: What it may spend in total
            (``coordination.decomposition_tree_max_sessions``), so the number
            beside it is readable as progress rather than as a bare count.
        deepest_level: The deepest level reached, zero-based, so a tree still
            widening its first level is distinguishable from one recursing.
        units_planned: Subtasks written across every level so far.
        updated_at: When this snapshot was taken (tz-aware UTC). What makes a
            working decomposition distinguishable from a stalled one, which is
            the whole question an operator has while the count reads zero.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    sessions_spent: int = Field(ge=0, description="Planning sessions consumed")
    sessions_limit: int = Field(ge=0, description="Planning sessions allowed")
    deepest_level: int = Field(ge=0, description="Deepest level reached, zero-based")
    units_planned: int = Field(ge=0, description="Subtasks written so far")
    updated_at: AwareDatetime = Field(
        description="When this snapshot was taken (tz-aware UTC)",
    )

    @model_validator(mode="after")
    def _spend_within_its_limit(self) -> DecompositionProgress:
        """Refuse a snapshot claiming more spend than the run was allowed.

        The pair is rendered to an operator as "12 of 40", so the relationship
        is what makes either number mean anything. It holds today because one
        producer derives the spend by subtraction from a bounded remainder,
        which is correctness by construction of a different class; asserted
        here it survives a second producer.

        Returns:
            The validated snapshot.

        Raises:
            ValueError: The spend exceeds a declared limit.
        """
        if (
            self.sessions_limit != NO_SESSION_LIMIT
            and self.sessions_spent > self.sessions_limit
        ):
            msg = (
                f"sessions_spent {self.sessions_spent} exceeds "
                f"sessions_limit {self.sessions_limit}"
            )
            raise ValueError(msg)
        return self


__all__ = ["NO_SESSION_LIMIT", "DecompositionProgress"]
