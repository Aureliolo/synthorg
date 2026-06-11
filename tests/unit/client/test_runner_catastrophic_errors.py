"""Catastrophic-error carve-out for :class:`SimulationRunner._review_one`.

The review path absorbs broad ``Exception`` from the feedback sink so
a misbehaving downstream listener cannot poison the run. ``MemoryError``
and ``RecursionError`` are catastrophic interpreter state and MUST
escape that net so the surrounding orchestrator can fail fast.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from synthorg.client.config import SimulationRunnerConfig
from synthorg.client.models import ClientFeedback, TaskRequirement
from synthorg.client.runner import SimulationRunner
from synthorg.core.task_enums import Complexity, Priority, TaskType
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.intake.models import IntakeResult


def _make_feedback() -> ClientFeedback:
    """Minimal accepted feedback fixture for the review path."""
    return ClientFeedback(
        feedback_id="fb-1",
        task_id="task-1",
        client_id="client-1",
        accepted=True,
        reason="ok",
        created_at=datetime.now(UTC),
    )


class _StubClient:
    """Bare-minimum ``ClientInterface`` shape for the review path."""

    @property
    def client_id(self) -> str:
        return "client-1"

    async def review_deliverable(self, context: object) -> ClientFeedback:
        del context
        return _make_feedback()

    async def generate_requirements(
        self, context: object
    ) -> tuple[TaskRequirement, ...]:
        del context
        return ()


def _runner_config() -> SimulationRunnerConfig:
    return SimulationRunnerConfig(
        max_concurrent_tasks=1,
        task_timeout_sec=5.0,
        review_timeout_sec=5.0,
    )


def _requirement() -> TaskRequirement:
    return TaskRequirement(
        title="t",
        description="d",
        task_type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        estimated_complexity=Complexity.MEDIUM,
        acceptance_criteria=(),
    )


def _accepted_intake() -> IntakeResult:
    return IntakeResult.accepted_result(
        request_id="req-1",
        task_id="task-1",
    )


@pytest.mark.unit
class TestReviewOneCatastrophicErrors:
    """``_review_one`` propagates catastrophic errors from the feedback sink."""

    @pytest.mark.parametrize("exc_cls", [MemoryError, RecursionError])
    async def test_feedback_sink_catastrophic_propagates(
        self,
        exc_cls: type[BaseException],
    ) -> None:
        """A ``feedback_sink`` raising ``MemoryError`` / ``RecursionError``
        must escape the broad ``except Exception`` instead of being
        absorbed -- the surrounding execution surface needs to see the
        catastrophic interpreter state.
        """

        async def _raising_sink(feedback: ClientFeedback) -> None:
            del feedback
            raise exc_cls

        runner = SimulationRunner(
            config=_runner_config(),
            intake_engine=IntakeEngine(strategy=_NoopStrategy()),
            feedback_sink=_raising_sink,
        )

        with pytest.raises(exc_cls):
            await runner._review_one(
                semaphore=asyncio.Semaphore(1),
                client=_StubClient(),  # type: ignore[arg-type]
                requirement=_requirement(),
                result=_accepted_intake(),
            )

    async def test_feedback_sink_broad_exception_absorbed(self) -> None:
        """Pin the contract the catastrophic carve-out is layered on:
        ordinary ``Exception`` from the sink is swallowed (logged via
        ``log_exception_redacted``) and the review still returns the
        feedback's ``accepted`` flag."""

        async def _ordinary_failing_sink(feedback: ClientFeedback) -> None:
            del feedback
            msg = "downstream listener broken"
            raise RuntimeError(msg)

        runner = SimulationRunner(
            config=_runner_config(),
            intake_engine=IntakeEngine(strategy=_NoopStrategy()),
            feedback_sink=_ordinary_failing_sink,
        )

        accepted = await runner._review_one(
            semaphore=asyncio.Semaphore(1),
            client=_StubClient(),  # type: ignore[arg-type]
            requirement=_requirement(),
            result=_accepted_intake(),
        )
        # ``_review_one`` returns the feedback's ``accepted`` flag; the
        # raising sink does not change the review verdict.
        assert accepted is True


class _NoopStrategy:
    """Minimal :class:`IntakeStrategy` shape; the review path never calls it."""

    async def process(self, request: object) -> IntakeResult:
        del request
        return IntakeResult.rejected_result(request_id="x", reason="noop")
