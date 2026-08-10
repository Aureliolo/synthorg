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

from datetime import UTC, datetime
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, field_validator

from evals.models.brief import Brief
from evals.runner.log_tap import capture_run_logs
from evals.runner.metrics import RunMetrics, run_metrics
from evals.scoring.penalties import DEFAULT_PENALTY_TABLE
from synthorg.core.agent import AgentIdentity
from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.project import Project
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
from synthorg.persistence.project_protocol import ProjectRepository

logger = get_logger(__name__)

#: Human-readable name of the project every brief task belongs to.
EVAL_PROJECT_NAME: Final[str] = "Eval Benchmark"

#: Id of the project every brief task is attributed to. ``AgentEngine.run`` binds
#: it into the correlation context, and the sandbox backends read it from there
#: to pick the workspace subtree they mount, so anything provisioning a run's
#: workspace has to lay it out under this same id (which is why a kept workspace
#: has a UUID for a directory name rather than a readable one).
#:
#: A UUID rather than a label because the project is a real row: a task that
#: expects artifacts is a work task, and the engine refuses to run one against a
#: project it cannot look up, which is a correctness and membership check rather
#: than a formality. ``Project.id`` is a UUID, so a readable id could never
#: resolve and the lookup would fail on every run.
EVAL_PROJECT_ID: Final[UUID] = uuid5(NAMESPACE_URL, "synthorg-eval-benchmark")
EVAL_TASK_PROJECT: Final[str] = str(EVAL_PROJECT_ID)


def eval_project() -> Project:
    """Build the project every brief task runs under.

    Team is deliberately empty: the engine reads it as "no membership
    restriction", which is what a single-agent benchmark project means. Naming a
    team would bind this to one agent id, and the golden-company suite and the
    loop A/B run under different ones.

    Returns:
        The benchmark :class:`~synthorg.core.project.Project`.
    """
    now = datetime.now(UTC)
    return Project(
        id=EVAL_PROJECT_ID,
        name=NotBlankStr(EVAL_PROJECT_NAME),
        description="Synthetic project the eval briefs execute under.",
        created_at=now,
        updated_at=now,
    )


async def seed_eval_project(repo: ProjectRepository) -> None:
    """Make the benchmark project resolvable in *repo*, idempotently.

    ``save`` rather than ``create`` because a repository reused across runs
    already carries the row, and re-seeding it is not a lifecycle transition
    anyone audits.

    Args:
        repo: The project repository the engine will validate against.
    """
    await repo.save(eval_project())


def brief_task_id(brief_id: str) -> UUID:
    """Derive the task id a brief's run is attributed to.

    Derived from the brief alone so a re-run of the same brief lands on the same
    task. Exported because the A/B recorder mints its gateway bearer against
    this id from outside the engine, and a second copy of the formula would let
    the two sides drift into attributing one run's cost to two tasks.

    Args:
        brief_id: The brief's identifier.

    Returns:
        The deterministic task id.
    """
    return uuid5(NAMESPACE_URL, f"eval-{brief_id}")


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


def _expected_artifacts(brief: Brief) -> tuple[ExpectedArtifact, ...]:
    """Project a brief's declared artifacts onto the task, where they are its own.

    Declaring these arms the loops' zero-artifact guard: a COMPLETED run that
    called no tool is reclassified ``NO_OP`` only for a task that expected
    something. Without them a loop that wrote nothing reads as a clean success,
    and a NO_OP rate measured over such runs is zero because nothing could raise
    it.

    Gated on the workspace block rather than on the artifact list. A
    workspace-graded brief hands the loop a real directory and grades what it
    left there, so its declared artifacts are the loop's own output. Every other
    kind has its deliverable text materialised into those paths by the runner
    after the fact, so the same declaration would demand of the loop something
    the harness itself produces.

    Returns:
        One expected artifact per declaration, or empty for a brief whose
        artifacts are not the loop's to produce.
    """
    if brief.workspace is None:
        return ()
    # The loops gate on presence rather than on the type, so this labels the
    # declaration rather than deciding anything.
    return tuple(
        ExpectedArtifact(
            type=(
                ArtifactType.DOCUMENTATION
                if artifact.kind == "report"
                else ArtifactType.CODE
            ),
            path=artifact.path,
        )
        for artifact in brief.expected_artifacts
    )


def _brief_task(brief: Brief, *, agent_id: str) -> Task:
    """Build the task the engine executes for *brief*.

    The brief title becomes the task title, which the engine's context
    injection uses as the memory-retrieval anchor.

    Returns:
        The :class:`~synthorg.core.task.Task` for the brief.
    """
    return Task(
        id=brief_task_id(brief.brief_id),
        title=brief.title,
        description=brief.description,
        type=TaskType.DEVELOPMENT,
        project=EVAL_TASK_PROJECT,
        created_by="eval-runner",
        assigned_to=agent_id,
        artifacts_expected=_expected_artifacts(brief),
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

    with capture_run_logs() as logs:
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


__all__ = [
    "EVAL_PROJECT_ID",
    "EVAL_PROJECT_NAME",
    "EVAL_TASK_PROJECT",
    "BriefRunOutcome",
    "brief_task_id",
    "eval_project",
    "run_brief",
    "seed_eval_project",
]
