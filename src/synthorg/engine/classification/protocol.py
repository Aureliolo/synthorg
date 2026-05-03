"""Detector protocol and detection context for the classification pipeline.

Defines the pluggable ``Detector`` interface, detection scope-aware
context model, context loader protocol, and downstream classification
sink protocol.
"""

from typing import TYPE_CHECKING, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.budget.coordination_config import (
    DetectionScope,
    ErrorCategory,
)
from synthorg.communication.delegation.models import (
    DelegationRequest,  # noqa: TC001
)
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.classification.models import ErrorFinding  # noqa: TC001
from synthorg.engine.loop_protocol import ExecutionResult  # noqa: TC001
from synthorg.engine.review.models import PipelineResult  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.engine.classification.models import ClassificationResult


class DetectionContext(BaseModel):
    """Context provided to detectors during classification.

    Always carries the primary ``execution_result``, ``agent_id``,
    and ``task_id``.  Fields for TASK_TREE scope are empty tuples
    when the scope is SAME_TASK.

    Attributes:
        execution_result: The completed execution to analyse.
        agent_id: Agent that executed the task.
        task_id: Task that was executed.
        scope: Detection scope level for this context.
        delegate_executions: Execution results from delegate agents
            (TASK_TREE scope only).
        review_results: Review pipeline results for the task tree
            (TASK_TREE scope only).
        delegation_requests: Delegation requests in the task tree
            (TASK_TREE scope only).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    execution_result: ExecutionResult = Field(
        description="Completed execution to analyse",
    )
    agent_id: NotBlankStr = Field(description="Agent identifier")
    task_id: NotBlankStr = Field(description="Task identifier")
    scope: DetectionScope = Field(description="Detection scope level")
    delegate_executions: tuple[ExecutionResult, ...] = Field(
        default=(),
        description="Delegate agent executions (TASK_TREE scope)",
    )
    review_results: tuple[PipelineResult, ...] = Field(
        default=(),
        description="Review pipeline results (TASK_TREE scope)",
    )
    delegation_requests: tuple[DelegationRequest, ...] = Field(
        default=(),
        description="Delegation requests (TASK_TREE scope)",
    )

    @model_validator(mode="after")
    def _validate_scope_fields(self) -> Self:
        """Enforce scope-field consistency.

        ``SAME_TASK`` contexts must not carry task-tree data;
        populating those fields would be a loader bug and could
        confuse detectors that expect empty tuples in SAME_TASK
        mode.
        """
        if self.scope == DetectionScope.SAME_TASK and (
            self.delegate_executions or self.review_results or self.delegation_requests
        ):
            msg = (
                "SAME_TASK scope must not populate TASK_TREE fields "
                "(delegate_executions, review_results, "
                "delegation_requests)"
            )
            raise ValueError(msg)
        return self


@runtime_checkable
class Detector(Protocol):
    """Protocol for pluggable error detectors.

    Each detector targets a single ``ErrorCategory`` and declares
    which detection scopes it supports.  The pipeline provides the
    richest ``DetectionContext`` required by the configured scope.
    """

    @property
    def category(self) -> ErrorCategory:
        """Error category this detector targets."""
        ...

    @property
    def supported_scopes(self) -> frozenset[DetectionScope]:
        """Detection scopes this detector can operate on."""
        ...

    async def detect(
        self,
        context: DetectionContext,
    ) -> tuple[ErrorFinding, ...]:
        """Run detection and return findings.

        Args:
            context: Detection context with execution data.

        Returns:
            Tuple of error findings (empty if none detected).
        """
        ...


@runtime_checkable
class ScopedContextLoader(Protocol):
    """Protocol for loading detection context at a given scope."""

    async def load(
        self,
        execution_result: ExecutionResult,
        agent_id: NotBlankStr,
        task_id: NotBlankStr,
    ) -> DetectionContext:
        """Build a detection context for the configured scope.

        Args:
            execution_result: Primary execution result.
            agent_id: Agent identifier.
            task_id: Task identifier.

        Returns:
            Detection context populated for the loader's scope.
        """
        ...


@runtime_checkable
class ClassificationSink(Protocol):
    """Protocol for downstream consumers of classification results."""

    async def on_classification(
        self,
        result: ClassificationResult,
    ) -> None:
        """Receive a completed classification result.

        Implementations must be best-effort: log errors internally
        and never raise (except ``MemoryError`` / ``RecursionError``).

        Args:
            result: The completed classification result.
        """
        ...
