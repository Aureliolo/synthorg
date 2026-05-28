"""Protocol for pluggable criteria decomposition strategies."""

from typing import Protocol, runtime_checkable

from synthorg.core.task import AcceptanceCriterion
from synthorg.core.types import NotBlankStr
from synthorg.engine.quality.verification import AtomicProbe


@runtime_checkable
class CriteriaDecomposer(Protocol):
    """Protocol for decomposing acceptance criteria into atomic probes.

    Implementations take a tuple of acceptance criteria and produce
    binary yes/no probes suitable for rubric-based grading.
    """

    @property
    def name(self) -> str:
        """Strategy name for logging and config discrimination."""
        ...

    async def decompose(
        self,
        criteria: tuple[AcceptanceCriterion, ...],
        *,
        task_id: NotBlankStr,
        agent_id: NotBlankStr,
    ) -> tuple[AtomicProbe, ...]:
        """Decompose acceptance criteria into atomic probes.

        Args:
            criteria: Acceptance criteria to decompose.
            task_id: Task identifier for context.
            agent_id: Agent identifier for provider selection.

        Returns:
            Tuple of atomic binary probes.
        """
        ...
