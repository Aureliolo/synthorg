"""Protocols and models for shadow evaluation.

The shadow evaluation guard runs an adapted agent (the proposal applied
locally, not persisted) against a sample task suite and compares the
outcome to a baseline run of the current agent.  The guard composes
three pluggable pieces:

* ``ShadowTaskProvider`` -- sources the sample task suite.
* ``ShadowAgentRunner`` -- executes a single task against a given
  identity and (optionally) a pending proposal, returning a structured
  outcome.

The runner is owned by the caller because "how to run an agent" is
deployment-specific and because the guard must stay decoupled from the
full ``AgentEngine`` graph.  Production wiring adapts the runner to
``AgentEngine.run``; unit tests supply a deterministic fake.
"""

from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001

if TYPE_CHECKING:
    from typing import Self

    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task
    from synthorg.engine.evolution.models import AdaptationProposal


class ShadowTaskOutcome(BaseModel):
    """Result of running one task through a shadow agent runner.

    Attributes:
        success: Whether the task completed successfully.
        quality_score: Optional quality grade in ``[0, 1]`` -- higher is
            better.  ``None`` signals the runner did not grade the output.
        error: Short error message when ``success`` is False.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    success: bool = Field(description="Task completed successfully")
    quality_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional quality grade in [0, 1]",
    )
    error: NotBlankStr | None = Field(
        default=None,
        description="Error message when success is False",
    )

    @model_validator(mode="after")
    def _validate_error_matches_success(self) -> Self:
        """Ensure error is set iff success is False."""
        if self.success and self.error is not None:
            msg = "error must be None when success is True"
            raise ValueError(msg)
        if not self.success and self.error is None:
            msg = "error is required when success is False"
            raise ValueError(msg)
        return self


# audit-keep 2026-05-10: 2 impls in shadow_providers.py (Configured/Recent)
# + ShadowEvaluationGuard consumer; plug-in extension surface.
class ShadowTaskProvider(Protocol):
    """Sources the sample task suite for a shadow evaluation run."""

    @property
    def name(self) -> str:
        """Human-readable provider name."""
        ...

    async def sample(
        self,
        *,
        agent_id: NotBlankStr,
        sample_size: int,
    ) -> tuple[Task, ...]:
        """Return up to ``sample_size`` representative tasks for ``agent_id``.

        Implementations may return fewer tasks than ``sample_size`` (e.g.
        when the curated suite has only three tasks).  Returning an empty
        tuple causes the guard to reject the proposal -- shadow eval
        with no tasks cannot approve anything.

        Args:
            agent_id: Target agent identifier.
            sample_size: Upper bound on returned task count.

        Returns:
            Tuple of tasks to run in both baseline and adapted passes.
        """
        ...


# audit-keep 2026-05-10: vendor-injection surface; production wires to
# AgentEngine.run via caller-supplied adapter, tests supply a fake.
class ShadowAgentRunner(Protocol):
    """Executes one task against a given identity with optional proposal."""

    async def run(
        self,
        *,
        identity: AgentIdentity,
        proposal: AdaptationProposal | None,
        task: Task,
        timeout_seconds: float,
    ) -> ShadowTaskOutcome:
        """Run the agent on ``task`` using ``identity``.

        When ``proposal`` is provided the runner applies it in a
        sandboxed way (never persisting to the identity store or
        memory backend).  When ``proposal`` is ``None`` the baseline
        agent runs unmodified.

        The runner must honour ``timeout_seconds``; a timeout counts as
        a failed run (``success=False``) rather than propagating as an
        exception.

        Args:
            identity: Current agent identity.
            proposal: Optional pending adaptation proposal.
            task: The probe task to execute.
            timeout_seconds: Hard time budget for this single run.

        Returns:
            Structured outcome for the run.
        """
        ...
