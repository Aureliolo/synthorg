"""Intake engine strategy protocol.

Defines the pluggable interface for intake processing strategies.
"""

from typing import Protocol, runtime_checkable

from synthorg.client.models import ClientRequest
from synthorg.engine.intake.models import IntakeResult


@runtime_checkable
class IntakeStrategy(Protocol):
    """Protocol for intake processing strategies.

    Implementations process client requests through the intake
    pipeline, either creating tasks directly or routing through
    an agent-driven triage and scoping workflow.

    Error signaling contract:

    * Returns ``IntakeResult(accepted=True, task_id=...)`` when
      the request is accepted and a task is created.
    * Returns ``IntakeResult(accepted=False, rejection_reason=...)``
      when the request is rejected.
    """

    async def process(
        self,
        request: ClientRequest,
    ) -> IntakeResult:
        """Process a client request through the intake strategy.

        Args:
            request: The client request to process.

        Returns:
            Intake result indicating acceptance or rejection.
        """
        ...
