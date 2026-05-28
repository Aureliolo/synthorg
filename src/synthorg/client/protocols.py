"""Client simulation protocol definitions.

Defines the pluggable interfaces for client behavior, requirement
generation, feedback evaluation, reporting, and pool management.
All protocols are ``@runtime_checkable`` for structural subtyping.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import JsonValue

if TYPE_CHECKING:
    from synthorg.client.models import (
        ClientFeedback,
        ClientRequest,
        GenerationContext,
        PoolConstraints,
        ReviewContext,
        SimulationMetrics,
        TaskRequirement,
    )


@runtime_checkable
class ClientInterface(Protocol):
    """Unified interface for client agents (AI, human, or hybrid).

    Implementations handle both requirement submission and
    deliverable review. The simulation runner interacts with
    all client types through this single protocol.

    Error signaling contract:

    * ``submit_requirement`` returns ``None`` when the client
      declines to participate in the current round.
    * ``review_deliverable`` always returns a ``ClientFeedback``;
      rejection is expressed via ``accepted=False``.
    """

    async def submit_requirement(
        self,
        context: GenerationContext,
    ) -> TaskRequirement | None:
        """Generate or submit a task requirement.

        Args:
            context: Generation context with project and domain info.

        Returns:
            A task requirement, or ``None`` if the client declines.
        """
        ...

    async def review_deliverable(
        self,
        context: ReviewContext,
    ) -> ClientFeedback:
        """Review a completed task deliverable and provide feedback.

        Args:
            context: Review context with task details and deliverable.

        Returns:
            Client feedback with acceptance decision and reasoning.
        """
        ...


@runtime_checkable
class RequirementGenerator(Protocol):
    """Generates task requirements based on a strategy.

    Implementations may use templates, LLMs, datasets, or
    algorithmic approaches to produce requirements.
    """

    async def generate(
        self,
        context: GenerationContext,
    ) -> tuple[TaskRequirement, ...]:
        """Generate task requirements from the given context.

        Args:
            context: Generation context with project, domain, and count.

        Returns:
            Tuple of generated task requirements.
        """
        ...


@runtime_checkable
class FeedbackStrategy(Protocol):
    """Evaluates task deliverables and produces client feedback.

    Implementations range from simple binary accept/reject to
    multi-dimensional scoring and adversarial evaluation.
    """

    async def evaluate(
        self,
        context: ReviewContext,
    ) -> ClientFeedback:
        """Evaluate a task deliverable and produce feedback.

        Args:
            context: Review context with task and deliverable details.

        Returns:
            Client feedback with verdict, scores, and reasoning.
        """
        ...


@runtime_checkable
class ReportStrategy(Protocol):
    """Generates reports from simulation metrics.

    Implementations produce different report formats (detailed,
    summary, JSON export, metrics-only).
    """

    async def generate_report(
        self,
        metrics: SimulationMetrics,
    ) -> dict[str, JsonValue]:
        """Generate a report from simulation metrics.

        Args:
            metrics: Aggregated simulation metrics.

        Returns:
            Report data as a dictionary.
        """
        ...


@runtime_checkable
class ClientPoolStrategy(Protocol):
    """Selects clients from a pool based on constraints.

    Controls which clients participate in each simulation round.
    """

    async def select_clients(
        self,
        pool: tuple[ClientInterface, ...],
        constraints: PoolConstraints,
    ) -> tuple[ClientInterface, ...]:
        """Select clients from the pool based on constraints.

        Args:
            pool: Available client instances.
            constraints: Selection constraints.

        Returns:
            Tuple of selected clients.
        """
        ...


@runtime_checkable
class EntryPointStrategy(Protocol):
    """Routes client requests to the appropriate handler.

    Controls how client requests enter the intake pipeline
    (direct submission, project-based routing, queue-based).
    """

    async def route(
        self,
        request: ClientRequest,
    ) -> ClientRequest:
        """Route a client request through the entry point.

        Args:
            request: The client request to route.

        Returns:
            The routed request (potentially with updated status).
        """
        ...
