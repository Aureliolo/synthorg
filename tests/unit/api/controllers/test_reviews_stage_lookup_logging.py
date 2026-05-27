"""Tests for reviews controller stage-lookup error logging.

When ``task_engine.get_task`` raises ``KeyError``/``ValueError``, the
controller must log the exception type + scrubbed message under
``REVIEW_STAGE_LOOKUP_FAILED`` before silently coalescing into the
``None`` sentinel and routing through ``require_resource_or_404``.
Without this emission, the original exception class is lost and the
audit trail only carries a generic "task not found" line.

Litestar wraps controller methods with ``HTTPRouteHandler`` instances,
so ``controller.get_pipeline(...)`` resolves to the route-handler
``__call__`` (which expects request-scope kwargs, not the method's
parameters). Tests reach the underlying coroutine via ``.fn`` on the
class-level descriptor and bind it to the controller instance.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing
from litestar import Request, Router
from litestar.datastructures import State

from synthorg.api.controllers.reviews import (
    ReviewController,
    StageDecisionPayload,
)
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.core.domain_errors import NotFoundError
from synthorg.engine.review.pipeline import ReviewPipeline
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability.events.review_pipeline import (
    REVIEW_STAGE_LOOKUP_FAILED,
)
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def _make_state(*, task_engine: AsyncMock) -> MagicMock:
    """Build a State stub whose ``app_state`` carries the task engine."""
    pipeline = MagicMock(spec=ReviewPipeline)
    # ``MagicMock(spec=...)`` already creates a stubbed ``run`` method
    # for the ReviewPipeline.run coroutine; we only need to ensure
    # ``stages`` is a real iterable (the spec exposes it as a Mock
    # attribute by default).
    pipeline.stages = ()

    sim_state = MagicMock(spec=ClientSimulationState)
    sim_state.review_pipeline = pipeline

    app_state = make_app_state(
        task_engine=task_engine,
        client_simulation_state=sim_state,
    )

    state = MagicMock(spec=State)
    state.app_state = app_state
    return state


def _unwrap(method_name: str) -> Any:
    """Return the underlying coroutine function of a Litestar route handler."""
    handler = ReviewController.__dict__[method_name]
    return getattr(handler, "fn", handler)


async def test_get_pipeline_logs_when_task_engine_raises_keyerror() -> None:
    task_engine = AsyncMock(spec=TaskEngine)
    task_engine.get_task.side_effect = KeyError("missing")
    state = _make_state(task_engine=task_engine)
    controller = ReviewController(owner=MagicMock(spec=Router))
    fn = _unwrap("get_pipeline")

    with structlog.testing.capture_logs() as logs, pytest.raises(NotFoundError):
        await fn(controller, state=state, task_id="task-bogus")

    assert any(
        rec.get("event") == REVIEW_STAGE_LOOKUP_FAILED
        and rec.get("stage") == "run_pipeline"
        and rec.get("task_id") == "task-bogus"
        and rec.get("error_type") == "KeyError"
        and "KeyError" in (rec.get("error") or "")
        for rec in logs
    )


async def test_decide_stage_logs_when_task_engine_raises_valueerror() -> None:
    task_engine = AsyncMock(spec=TaskEngine)
    task_engine.get_task.side_effect = ValueError("invalid task id format")
    state = _make_state(task_engine=task_engine)
    controller = ReviewController(owner=MagicMock(spec=Router))
    fn = _unwrap("decide_stage")

    payload = MagicMock(spec=StageDecisionPayload)
    request = MagicMock(spec=Request)
    request.scope = {"user": None}
    request.app.plugins = []

    with structlog.testing.capture_logs() as logs, pytest.raises(NotFoundError):
        await fn(
            controller,
            request=request,
            state=state,
            task_id="task-bogus",
            stage_name="any-stage",
            data=payload,
        )

    assert any(
        rec.get("event") == REVIEW_STAGE_LOOKUP_FAILED
        and rec.get("stage") == "decide_stage"
        and rec.get("task_id") == "task-bogus"
        and rec.get("error_type") == "ValueError"
        and "ValueError" in (rec.get("error") or "")
        for rec in logs
    )
