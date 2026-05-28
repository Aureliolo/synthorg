"""Protocol for pluggable rubric grading strategies."""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.engine.quality.verification import (
    AtomicProbe,
    VerificationResult,
    VerificationRubric,
)
from synthorg.engine.workflow.handoff import HandoffArtifact


@runtime_checkable
class RubricGrader(Protocol):
    """Protocol for rubric-based grading of handoff artifacts.

    Implementations evaluate an artifact against a rubric and
    atomic probes, producing a structured verification result.
    """

    @property
    def name(self) -> str:
        """Strategy name for logging and config discrimination."""
        ...

    async def grade(
        self,
        *,
        artifact: HandoffArtifact,
        rubric: VerificationRubric,
        probes: tuple[AtomicProbe, ...],
        generator_agent_id: NotBlankStr,
        evaluator_agent_id: NotBlankStr,
    ) -> VerificationResult:
        """Grade an artifact against a rubric.

        Args:
            artifact: The handoff artifact to evaluate.
            rubric: Rubric with criteria and calibration examples.
            probes: Atomic binary probes from decomposition.
            generator_agent_id: Agent that produced the artifact.
            evaluator_agent_id: Agent performing the evaluation.

        Returns:
            Structured verification result with verdict.
        """
        ...
