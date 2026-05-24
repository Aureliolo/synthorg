"""Batch simulation runner."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from synthorg.client.config import SimulationRunnerConfig  # noqa: TC001
from synthorg.client.models import (
    ClientFeedback,
    ClientRequest,
    GenerationContext,
    ReviewContext,
    SimulationConfig,
    SimulationMetrics,
    TaskRequirement,
)
from synthorg.client.protocols import (
    ClientInterface,  # noqa: TC001
    ReportStrategy,  # noqa: TC001
)
from synthorg.engine.intake.engine import IntakeEngine  # noqa: TC001
from synthorg.engine.intake.models import IntakeResult  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.client import (
    CLIENT_FEEDBACK_SINK_FAILED,
    CLIENT_REQUEST_SUBMITTED,
    CLIENT_REQUIREMENT_GENERATED,
    CLIENT_REVIEW_COMPLETED,
    CLIENT_REVIEW_STARTED,
    SIMULATION_ROUND_COMPLETED,
    SIMULATION_RUN_COMPLETED,
    SIMULATION_RUN_STARTED,
)

FeedbackSink = Callable[[ClientFeedback], Awaitable[None]]

logger = get_logger(__name__)


class SimulationRunner:
    """Orchestrates a batch simulation end-to-end.

    Each :meth:`run` invocation drives ``sim_config.rounds`` rounds
    of generation, intake, and review against a fixed client pool.
    Metrics are aggregated across rounds and, when a
    :class:`ReportStrategy` is supplied, also rendered to a
    transport-agnostic report dict.
    """

    def __init__(
        self,
        *,
        config: SimulationRunnerConfig,
        intake_engine: IntakeEngine,
        report_strategy: ReportStrategy | None = None,
        feedback_sink: FeedbackSink | None = None,
    ) -> None:
        """Initialize the simulation runner.

        Args:
            config: Runner configuration (concurrency, timeouts).
            intake_engine: Intake engine processing each request.
            report_strategy: Optional report strategy for
                producing a final report dict.
            feedback_sink: Optional async callback invoked for every
                :class:`ClientFeedback` emitted during review;
                used by the API layer to populate the per-client
                satisfaction history.
        """
        self._config = config
        self._intake_engine = intake_engine
        self._report_strategy = report_strategy
        self._feedback_sink = feedback_sink

    async def run(
        self,
        *,
        sim_config: SimulationConfig,
        clients: tuple[ClientInterface, ...],
    ) -> tuple[SimulationMetrics, dict[str, Any] | None]:
        """Execute the full simulation loop.

        Args:
            sim_config: Per-run simulation configuration.
            clients: Tuple of clients participating in every round.

        Returns:
            Tuple of aggregated :class:`SimulationMetrics` and an
            optional report dict (``None`` if no strategy set).

        Raises:
            ValueError: If no clients are supplied.
        """
        if not clients:
            msg = "SimulationRunner.run requires at least one client"
            raise ValueError(msg)

        logger.info(
            SIMULATION_RUN_STARTED,
            simulation_id=sim_config.simulation_id,
            project_id=sim_config.project_id,
            rounds=sim_config.rounds,
        )

        metrics = _RunningTotals()
        for round_index in range(sim_config.rounds):
            round_metrics = await self._run_round(
                round_index=round_index,
                sim_config=sim_config,
                clients=clients,
            )
            metrics.accumulate(round_metrics)
            logger.info(
                SIMULATION_ROUND_COMPLETED,
                simulation_id=sim_config.simulation_id,
                round_number=round_metrics["round_number"],
                total_requirements=round_metrics["total_requirements"],
                tasks_created=round_metrics["tasks_created"],
                accepted=round_metrics["accepted"],
                rejected=round_metrics["rejected"],
            )

        final_metrics = metrics.freeze()
        report: dict[str, Any] | None = None
        if self._report_strategy is not None:
            report = await self._report_strategy.generate_report(final_metrics)

        logger.info(
            SIMULATION_RUN_COMPLETED,
            simulation_id=sim_config.simulation_id,
            tasks_created=final_metrics.total_tasks_created,
            acceptance_rate=final_metrics.acceptance_rate,
        )
        return final_metrics, report

    async def _run_round(
        self,
        *,
        round_index: int,
        sim_config: SimulationConfig,
        clients: tuple[ClientInterface, ...],
    ) -> dict[str, Any]:
        participants = clients[: sim_config.clients_per_round]
        context = self._build_generation_context(
            sim_config=sim_config,
        )

        requirements = await self._gather_requirements(
            participants=participants,
            context=context,
        )
        intake_results = await self._run_intake_batch(
            participants=participants,
            requirements=requirements,
        )
        review_outcomes = await self._run_reviews(
            participants=participants,
            requirements=requirements,
            intake_results=intake_results,
        )

        tasks_created = sum(1 for r in intake_results if r.accepted)
        tasks_accepted = sum(1 for o in review_outcomes if o is True)
        tasks_rejected = sum(1 for o in review_outcomes if o is False)
        return {
            "round_number": round_index + 1,
            "total_requirements": len(requirements),
            "tasks_created": tasks_created,
            "accepted": tasks_accepted,
            "rejected": tasks_rejected,
        }

    async def _gather_requirements(
        self,
        *,
        participants: tuple[ClientInterface, ...],
        context: GenerationContext,
    ) -> list[tuple[ClientInterface, TaskRequirement]]:
        async with asyncio.TaskGroup() as group:
            submission_tasks = [
                group.create_task(
                    self._submit_one(
                        client=client,
                        context=context,
                    ),
                )
                for client in participants
            ]
        collected: list[tuple[ClientInterface, TaskRequirement]] = []
        for client, task in zip(participants, submission_tasks, strict=True):
            requirement = task.result()
            if requirement is None:
                continue
            collected.append((client, requirement))
            logger.debug(
                CLIENT_REQUIREMENT_GENERATED,
                client_id=_client_id(client),
                title=requirement.title,
            )
        return collected

    async def _submit_one(
        self,
        *,
        client: ClientInterface,
        context: GenerationContext,
    ) -> TaskRequirement | None:
        return await client.submit_requirement(context)

    async def _run_intake_batch(
        self,
        *,
        participants: tuple[ClientInterface, ...],
        requirements: list[tuple[ClientInterface, TaskRequirement]],
    ) -> list[IntakeResult]:
        del participants
        if not requirements:
            return []
        async with asyncio.TaskGroup() as group:
            intake_tasks = [
                group.create_task(self._run_intake(client=client, requirement=req))
                for client, req in requirements
            ]
        return [task.result() for task in intake_tasks]

    async def _run_intake(
        self,
        *,
        client: ClientInterface,
        requirement: TaskRequirement,
    ) -> IntakeResult:
        request = ClientRequest(
            client_id=_client_id(client),
            requirement=requirement,
        )
        logger.debug(
            CLIENT_REQUEST_SUBMITTED,
            client_id=request.client_id,
            request_id=request.request_id,
        )
        _, result = await self._intake_engine.process(request)
        return result

    async def _run_reviews(
        self,
        *,
        participants: tuple[ClientInterface, ...],
        requirements: list[tuple[ClientInterface, TaskRequirement]],
        intake_results: list[IntakeResult],
    ) -> list[bool | None]:
        del participants
        semaphore = asyncio.Semaphore(max(1, self._config.max_concurrent_tasks))
        async with asyncio.TaskGroup() as group:
            review_tasks = [
                group.create_task(
                    self._review_one(
                        semaphore=semaphore,
                        client=client,
                        requirement=req,
                        result=intake,
                    )
                )
                for (client, req), intake in zip(
                    requirements, intake_results, strict=True
                )
            ]
        return [task.result() for task in review_tasks]

    async def _review_one(
        self,
        *,
        semaphore: asyncio.Semaphore,
        client: ClientInterface,
        requirement: TaskRequirement,
        result: IntakeResult,
    ) -> bool | None:
        if not result.accepted or result.task_id is None:
            return None
        async with semaphore:
            context = ReviewContext(
                task_id=result.task_id,
                task_title=requirement.title,
                deliverable_summary=requirement.description,
                acceptance_criteria=requirement.acceptance_criteria,
            )
            client_id = _client_id(client)
            logger.debug(
                CLIENT_REVIEW_STARTED,
                client_id=client_id,
                task_id=result.task_id,
            )
            feedback = await client.review_deliverable(context)
            logger.debug(
                CLIENT_REVIEW_COMPLETED,
                client_id=client_id,
                task_id=result.task_id,
                accepted=feedback.accepted,
            )
            if self._feedback_sink is not None:
                try:
                    await self._feedback_sink(feedback)
                except Exception as exc:
                    logger.error(
                        CLIENT_FEEDBACK_SINK_FAILED,
                        client_id=client_id,
                        task_id=result.task_id,
                        accepted=feedback.accepted,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
            return feedback.accepted

    @staticmethod
    def _build_generation_context(
        *,
        sim_config: SimulationConfig,
    ) -> GenerationContext:
        return GenerationContext(
            project_id=sim_config.project_id,
            domain="simulation",
            count=sim_config.requirements_per_client,
        )


class _RunningTotals:
    """Mutable helper that aggregates per-round stats."""

    def __init__(self) -> None:
        self._total_requirements = 0
        self._total_tasks_created = 0
        self._tasks_accepted = 0
        self._tasks_rejected = 0
        self._round_snapshots: list[dict[str, Any]] = []

    def accumulate(self, round_metrics: dict[str, Any]) -> None:
        self._total_requirements += int(round_metrics["total_requirements"])
        self._total_tasks_created += int(round_metrics["tasks_created"])
        self._tasks_accepted += int(round_metrics["accepted"])
        self._tasks_rejected += int(round_metrics["rejected"])
        self._round_snapshots.append(round_metrics)

    def freeze(self) -> SimulationMetrics:
        return SimulationMetrics(
            total_requirements=self._total_requirements,
            total_tasks_created=self._total_tasks_created,
            tasks_accepted=self._tasks_accepted,
            tasks_rejected=self._tasks_rejected,
            round_metrics=tuple(self._round_snapshots),
        )


def _client_id(client: ClientInterface) -> str:
    """Best-effort client id extraction for logging."""
    profile = getattr(client, "profile", None)
    client_id = getattr(profile, "client_id", None)
    return client_id or "anonymous"
