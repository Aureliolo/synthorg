"""Intake engine strategy protocol.

Defines the pluggable interface for intake processing strategies.
"""

from typing import Protocol, runtime_checkable

from synthorg.client.models import ClientRequest
from synthorg.core.task import Task
from synthorg.engine.intake.models import IntakeResult
from synthorg.engine.task_engine_models import CreateTaskData


@runtime_checkable
class TaskCreator(Protocol):
    """Minimal task-creation collaborator used by intake strategies.

    Intake strategies need only the ability to create a task on
    acceptance. Depending on this structural protocol rather than the
    concrete ``TaskEngine`` keeps the strategies decoupled from the
    engine's full surface and lets tests substitute a lightweight
    double without inheriting the concrete class.
    """

    async def create_task(
        self,
        data: CreateTaskData,
        *,
        requested_by: str,
    ) -> Task:
        """Create a task and return it.

        Args:
            data: Task creation data.
            requested_by: Identity of the requester.

        Returns:
            The created task.
        """
        ...


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
