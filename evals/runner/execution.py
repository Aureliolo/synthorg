# module-kind: code
"""Per-brief execution: run one brief through a direct ``AgentEngine``.

The runner boots a real agent engine with a deterministic provider, injects any
retrieved procedural memory (the live ``capture -> store -> retrieve -> inject``
pipeline), runs the brief as a task, and captures the process-fact events the
scorer's penalty table tracks. Only the LLM is a deterministic stand-in.
"""

from pydantic import BaseModel, ConfigDict
from structlog.testing import capture_logs

from evals.models.brief import Brief
from evals.scoring.penalties import DEFAULT_PENALTY_TABLE
from synthorg.core.agent import AgentIdentity
from synthorg.core.enums import TaskStatus, TaskType
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.run_result import AgentRunResult
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever import ContextInjectionStrategy
from synthorg.observability import get_logger
from synthorg.observability.events.evals import EVALS_BRIEF_RUN_COMPLETE
from synthorg.providers.models import ChatMessage

logger = get_logger(__name__)

# Token budget for the memory-injection retrieval step. Generous enough to
# admit the handful of procedural lessons a benchmark agent accumulates without
# crowding the deterministic prompt; tuning lives here, not in brief YAML.
_MEMORY_TOKEN_BUDGET: int = 4000


class BriefRunOutcome(BaseModel):
    """Result of running one brief through the engine.

    Carries the raw agent result, the deliverable text the judge scores, and
    the per-class counts of process-fact events the penalty table tracks.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    brief_id: NotBlankStr
    termination_reason: NotBlankStr
    deliverable_text: str | None
    tracked_events: dict[str, int]


async def _retrieve_memory_messages(
    memory_backend: MemoryBackend | None,
    *,
    agent_id: str,
    query_text: str,
) -> tuple[ChatMessage, ...]:
    """Retrieve + format procedural memory for injection (empty when absent).

    Returns:
        Formatted memory messages, or an empty tuple when no backend/query.
    """
    if memory_backend is None or not query_text:
        return ()
    strategy = ContextInjectionStrategy(
        backend=memory_backend,
        config=MemoryRetrievalConfig(),
    )
    return await strategy.prepare_messages(
        NotBlankStr(agent_id),
        NotBlankStr(query_text),
        token_budget=_MEMORY_TOKEN_BUDGET,
    )


def _brief_task(brief: Brief, *, agent_id: str) -> Task:
    """Build the task the engine executes for *brief*.

    Returns:
        The :class:`~synthorg.core.task.Task` for the brief.
    """
    return Task(
        id=NotBlankStr(f"eval-{brief.brief_id}"),
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
    memory_backend: MemoryBackend | None,
    retrieval_query: str | None = None,
) -> BriefRunOutcome:
    """Run *brief* through *engine* and capture its outcome + process facts.

    Args:
        engine: The booted agent engine (provider + optional memory pipeline).
        brief: The exam item to run.
        identity: The stable agent identity (reused across rounds so memory
            accumulates per agent).
        memory_backend: Backend to retrieve procedural memory from for
            injection (``None`` disables injection).
        retrieval_query: Memory-retrieval query text; defaults to the brief
            title (the salient task token for relevance matching).

    Returns:
        The brief's run outcome (termination, deliverable, tracked events).
    """
    agent_id = str(identity.id)
    query = retrieval_query if retrieval_query is not None else brief.title
    memory_messages = await _retrieve_memory_messages(
        memory_backend, agent_id=agent_id, query_text=query
    )
    task = _brief_task(brief, agent_id=agent_id)

    with capture_logs() as logs:
        result: AgentRunResult = await engine.run(
            identity=identity,
            task=task,
            max_turns=brief.limits.max_turns,
            memory_messages=memory_messages,
        )

    tracked: dict[str, int] = {}
    for entry in logs:
        event = entry.get("event")
        if isinstance(event, str) and DEFAULT_PENALTY_TABLE.is_tracked(event):
            tracked[event] = tracked.get(event, 0) + 1

    logger.info(
        EVALS_BRIEF_RUN_COMPLETE,
        brief_id=brief.brief_id,
        termination_reason=result.termination_reason.value,
        tracked_event_count=sum(tracked.values()),
    )
    return BriefRunOutcome(
        brief_id=brief.brief_id,
        termination_reason=NotBlankStr(result.termination_reason.value),
        deliverable_text=result.completion_summary,
        tracked_events=tracked,
    )


__all__ = ["BriefRunOutcome", "run_brief"]
