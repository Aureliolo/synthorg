# module-kind: declarative
"""The record that a feature's request was served by its alternate.

The event log says a failover happened; this survives the restart it does
not. An operator reading a cost row, a latency spike or an odd answer a week
later needs to know which connection served that request, and the setting
only ever says which one was allowed to.

Both pairs are recorded in full rather than "the declared one plus a flag":
once the route map has been edited, "the alternate" no longer identifies
anything.
"""

from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.providers.health import ProviderOutcomeClass

#: Where in a dispatch the alternate took over. ``preflight`` means the
#: declared pair was already known unserviceable and never tried; ``retry``
#: means it was tried, failed on a class the alternate might survive, and
#: the one retry ran there.
FailoverStage = Literal["preflight", "retry"]


class ProviderFailoverEvent(BaseModel):
    """One dispatch served by the operator's declared alternate.

    Attributes:
        id: Stable row identifier.
        occurred_at: When the alternate served.
        feature: The system feature whose ``MODEL_REF`` setting was bound to
            the declared pair, so an operator can tell which capability was
            affected without correlating timestamps.
        declared_provider: Connection the operator bound the feature to.
        declared_model: Model on that connection.
        served_provider: Connection that actually answered.
        served_model: Model that actually answered.
        trigger_class: Outcome class that caused the switch.
        trigger_stage: Whether the declared pair was skipped or retried past.
        agent_id: Agent in scope, when the dispatch ran inside one. Never
            invented: work the system does for itself belongs to no agent.
        task_id: Task in scope, on the same terms.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4, description="Row identifier")
    occurred_at: AwareDatetime = Field(description="When the alternate served")
    feature: NotBlankStr = Field(description="System feature that dispatched")
    declared_provider: NotBlankStr = Field(description="Operator's bound connection")
    declared_model: NotBlankStr = Field(description="Operator's bound model")
    served_provider: NotBlankStr = Field(description="Connection that answered")
    served_model: NotBlankStr = Field(description="Model that answered")
    trigger_class: ProviderOutcomeClass = Field(description="Failure that switched")
    trigger_stage: FailoverStage = Field(description="Skipped, or retried past")
    agent_id: NotBlankStr | None = Field(
        default=None,
        description="Agent in scope, when there was one",
    )
    task_id: NotBlankStr | None = Field(
        default=None,
        description="Task in scope, when there was one",
    )


__all__ = ["FailoverStage", "ProviderFailoverEvent"]
