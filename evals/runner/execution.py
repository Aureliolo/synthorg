# module-kind: code
"""Per-brief execution: run one brief through a direct ``AgentEngine``.

The runner boots a real agent engine with a deterministic provider, runs the
brief as a task, and captures the process-fact events the scorer's penalty
table tracks. When the engine is wired with a ``memory_injection_strategy`` and
a backend (see ``evals.run``), it surfaces accumulated procedural memory through
its OWN dispatch -- the runner does not pre-retrieve and pass memory in, so the
learning curve proves the live ``capture -> store -> retrieve -> inject``
pipeline. Only the LLM is a deterministic stand-in.
"""

from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, field_validator
from structlog.testing import capture_logs

from evals.models.brief import Brief
from evals.runner.metrics import RunMetrics, run_metrics
from evals.scoring.penalties import DEFAULT_PENALTY_TABLE
from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.run_result import AgentRunResult
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_BRIEF_RUN_COMPLETE,
    EVALS_PURPOSE_INVOKED_FIELD_MISSING,
)
from synthorg.observability.events.provider import PROVIDER_PROMPT_PURPOSE_INVOKED

logger = get_logger(__name__)


class BriefRunOutcome(BaseModel):
    """Result of running one brief through the engine.

    Carries the raw agent result, the deliverable text the judge scores, the
    per-class counts of process-fact events the penalty table tracks, and the
    per-run metrics (tokens, wall-clock, turns, tool profile, rework) the loop
    A/B ranks on.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    brief_id: NotBlankStr
    termination_reason: NotBlankStr
    deliverable_text: str | None
    tracked_events: dict[str, int]
    prompt_class_usage: dict[str, int]
    total_cost: float
    metrics: RunMetrics

    @field_validator("tracked_events", "prompt_class_usage")
    @classmethod
    def _counts_are_non_negative(cls, value: dict[str, int]) -> dict[str, int]:
        """Reject negative per-key counts so this DTO is a real boundary.

        Returns:
            The validated mapping.

        Raises:
            ValueError: If any count is negative.
        """
        for key, count in value.items():
            if count < 0:
                msg = f"count for {key!r} must be >= 0 (got {count})"
                raise ValueError(msg)
        return value


def _brief_task(brief: Brief, *, agent_id: str) -> Task:
    """Build the task the engine executes for *brief*.

    The brief title becomes the task title, which the engine's context
    injection uses as the memory-retrieval anchor.

    Returns:
        The :class:`~synthorg.core.task.Task` for the brief.
    """
    return Task(
        id=uuid5(NAMESPACE_URL, f"eval-{brief.brief_id}"),
        title=brief.title,
        description=brief.description,
        type=TaskType.DEVELOPMENT,
        project="eval-benchmark",
        created_by="eval-runner",
        assigned_to=agent_id,
        status=TaskStatus.ASSIGNED,
    )


async def run_brief(
    engine: AgentEngine,
    brief: Brief,
    *,
    identity: AgentIdentity,
) -> BriefRunOutcome:
    """Run *brief* through *engine* and capture its outcome + process facts.

    The engine injects any accumulated procedural memory itself (when wired
    with a ``memory_injection_strategy``); the runner does not pre-retrieve.

    Args:
        engine: The booted agent engine (provider + optional memory pipeline).
        brief: The exam item to run.
        identity: The stable agent identity (reused across rounds so memory
            accumulates per agent).

    Returns:
        The brief's run outcome (termination, deliverable, tracked events).
    """
    task = _brief_task(brief, agent_id=str(identity.id))

    with capture_logs() as logs:
        result: AgentRunResult = await engine.run(
            identity=identity,
            task=task,
            max_turns=brief.limits.max_turns,
        )

    tracked: dict[str, int] = {}
    prompt_class_usage: dict[str, int] = {}
    malformed_purpose_events = 0
    for entry in logs:
        event = entry.get("event")
        if not isinstance(event, str):
            continue
        if DEFAULT_PENALTY_TABLE.is_tracked(event):
            tracked[event] = tracked.get(event, 0) + 1
        elif event == PROVIDER_PROMPT_PURPOSE_INVOKED:
            prompt_class_id = entry.get("prompt_class_id")
            if isinstance(prompt_class_id, str):
                prompt_class_usage[prompt_class_id] = (
                    prompt_class_usage.get(prompt_class_id, 0) + 1
                )
            else:
                # The emit/read field name drifted or structlog reshaped the
                # entry: surface it rather than silently under-counting usage.
                malformed_purpose_events += 1

    if malformed_purpose_events:
        logger.warning(
            EVALS_PURPOSE_INVOKED_FIELD_MISSING,
            brief_id=brief.brief_id,
            dropped_count=malformed_purpose_events,
            reason="prompt_class_id_absent_or_wrong_type",
        )

    logger.info(
        EVALS_BRIEF_RUN_COMPLETE,
        brief_id=brief.brief_id,
        termination_reason=result.termination_reason.value,
        tracked_event_count=sum(tracked.values()),
        prompt_class_invocations=sum(prompt_class_usage.values()),
    )
    return BriefRunOutcome(
        brief_id=brief.brief_id,
        termination_reason=NotBlankStr(result.termination_reason.value),
        deliverable_text=result.completion_summary,
        tracked_events=tracked,
        prompt_class_usage=prompt_class_usage,
        total_cost=result.total_cost,
        metrics=run_metrics(
            result.execution_result, duration_seconds=result.duration_seconds
        ),
    )


__all__ = ["BriefRunOutcome", "run_brief"]
