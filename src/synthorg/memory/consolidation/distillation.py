"""Distillation request capture at task completion.

Captures trajectory summaries, outcomes, and the *names* of memory
tools the agent invoked during a task from execution results, then
stores an EPISODIC memory entry tagged ``"distillation"`` that
downstream consolidation strategies can read as trajectory context.
See ``DistillationRequest`` for why we capture tool *names* rather
than the IDs of entries those tools returned.
"""

import asyncio
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from synthorg.execution.turn import TurnRecord
from synthorg.memory.models import MemoryMetadata, MemoryStoreRequest
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.tool_retriever import (
    RECALL_MEMORY_TOOL_NAME,
    SEARCH_MEMORY_TOOL_NAME,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.consolidation import (
    DISTILLATION_CAPTURE_FAILED,
    DISTILLATION_CAPTURED,
)

logger = get_logger(__name__)

#: Tag applied to EPISODIC entries produced by ``capture_distillation``.
#: Downstream consolidation reads entries with this tag as trajectory context.
DISTILLATION_TAG: NotBlankStr = "distillation"


class MemoryToolName(StrEnum):
    """Names of memory tools an agent can invoke during execution.

    Used to narrow ``DistillationRequest.memory_tool_invocations`` from
    arbitrary strings to the known tool names, making downstream
    consumers type-safe.
    """

    SEARCH_MEMORY = "search_memory"
    """Agent searched for memories by query text."""

    RECALL_MEMORY = "recall_memory"
    """Agent recalled a specific memory by ID."""


class DistillationRequest(BaseModel):
    """Captured distillation data from a completed task execution.

    ``memory_tool_invocations`` records the names of memory tools the
    agent invoked (``"search_memory"``, ``"recall_memory"``) rather
    than the IDs of the specific entries those tools returned.  Actual
    entry IDs are internal to tool results and are not surfaced on
    ``TurnRecord``; consumers that need them must query the backend
    for entries tagged ``"distillation"`` and correlate by task.

    Attributes:
        agent_id: Which agent completed the task.
        task_id: Which task was completed.
        trajectory_summary: Summarized execution trajectory.
        outcome: Task outcome description.
        memory_tool_invocations: Names of memory tools invoked during
            execution (not memory entry IDs).
        created_at: Capture timestamp.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Agent that completed the task")
    task_id: NotBlankStr = Field(description="Completed task identifier")
    trajectory_summary: NotBlankStr = Field(
        description="Summarized execution trajectory",
    )
    outcome: NotBlankStr = Field(description="Task outcome description")
    memory_tool_invocations: tuple[MemoryToolName, ...] = Field(
        default=(),
        description=(
            "Names of memory tools invoked during execution "
            "(e.g. 'search_memory', 'recall_memory')"
        ),
    )
    created_at: AwareDatetime = Field(description="Capture timestamp")


_MEMORY_TOOL_NAME_MAP: dict[str, MemoryToolName] = {
    SEARCH_MEMORY_TOOL_NAME: MemoryToolName.SEARCH_MEMORY,
    RECALL_MEMORY_TOOL_NAME: MemoryToolName.RECALL_MEMORY,
}


def build_trajectory_summary(turns: tuple[TurnRecord, ...]) -> str:
    """Build a trajectory summary from turn records.

    Args:
        turns: Per-turn metadata from the execution.

    Returns:
        Human-readable trajectory summary.
    """
    if not turns:
        return "No turns recorded."

    total_tokens = sum(t.total_tokens for t in turns)
    tool_call_count = sum(len(turn.tool_calls_made) for turn in turns)
    unique_tools = sorted({tool for turn in turns for tool in turn.tool_calls_made})

    turn_word = "turn" if len(turns) == 1 else "turns"
    parts = [f"{len(turns)} {turn_word}, {total_tokens} tokens"]
    if unique_tools:
        parts.append(f"tools: {', '.join(unique_tools)}")
    if tool_call_count:
        parts.append(f"{tool_call_count} tool calls total")

    return "; ".join(parts)


def build_outcome(
    termination_reason: TerminationReason,
    error_message: str | None,
) -> str:
    """Build an outcome description from termination metadata.

    Args:
        termination_reason: Why the execution loop stopped.
        error_message: Error description (when reason is ERROR).

    Returns:
        Human-readable outcome string.
    """
    if termination_reason == TerminationReason.COMPLETED:
        return "Task completed successfully."
    if termination_reason == TerminationReason.ERROR and error_message:
        return f"Task failed: {error_message}"
    return f"Task terminated: {termination_reason.value}"


def extract_memory_tool_invocations(
    turns: tuple[TurnRecord, ...],
) -> tuple[MemoryToolName, ...]:
    """Extract memory tool invocation names from turn records.

    Scans turns for invocations of ``search_memory`` or
    ``recall_memory`` and returns the typed tool names (one per
    invocation).  These are NOT memory entry IDs -- ``TurnRecord``
    does not surface the IDs of entries each tool call returned.

    Args:
        turns: Per-turn metadata from the execution.

    Returns:
        Tuple of memory-related tool call names, preserving invocation
        order and counting repeats.
    """
    return tuple(
        _MEMORY_TOOL_NAME_MAP[tool_name]
        for turn in turns
        for tool_name in turn.tool_calls_made
        if tool_name in _MEMORY_TOOL_NAME_MAP
    )


def _build_distillation_request(
    execution_result: ExecutionResult,
    agent_id: NotBlankStr,
    task_id: NotBlankStr,
) -> DistillationRequest:
    """Assemble a :class:`DistillationRequest` from an execution result.

    Args:
        execution_result: The completed execution result.
        agent_id: Agent that ran the task.
        task_id: Task identifier.

    Returns:
        The assembled ``DistillationRequest``.
    """
    trajectory = build_trajectory_summary(execution_result.turns)
    outcome = build_outcome(
        execution_result.termination_reason,
        execution_result.error_message,
    )
    tool_invocations = extract_memory_tool_invocations(execution_result.turns)
    return DistillationRequest(
        agent_id=agent_id,
        task_id=task_id,
        trajectory_summary=trajectory,
        outcome=outcome,
        memory_tool_invocations=tool_invocations,
        created_at=datetime.now(UTC),
    )


def _render_store_content(request: DistillationRequest) -> str:
    """Render the human-readable content stored on the backend.

    Includes ``task_id`` so downstream readers can correlate distillation
    entries by task, plus outcome, trajectory, and the memory tool
    invocation names (not entry IDs).

    Returns:
        Result of type ``str``.
    """
    tool_names = (
        ", ".join(request.memory_tool_invocations)
        if request.memory_tool_invocations
        else "none"
    )
    return (
        f"Task ID: {request.task_id}\n"
        f"Outcome: {request.outcome}\n"
        f"Trajectory: {request.trajectory_summary}\n"
        f"Memory tool invocations: {tool_names}"
    )


async def capture_distillation(
    execution_result: ExecutionResult,
    agent_id: NotBlankStr,
    task_id: NotBlankStr,
    *,
    backend: MemoryBackend,
) -> DistillationRequest | None:
    """Capture distillation data at task completion.

    Non-critical -- returns ``None`` on non-system failures and logs
    a warning.  The captured data is stored as an EPISODIC memory
    entry tagged with ``"distillation"`` so downstream consolidation
    strategies (notably ``LLMConsolidationStrategy``) can read it as
    trajectory context.

    Args:
        execution_result: The completed execution result.
        agent_id: Agent that ran the task.
        task_id: Task identifier.
        backend: Memory backend for storing the distillation entry.

    Returns:
        The captured ``DistillationRequest``, or ``None`` on any
        non-system failure (system errors propagate per Raises).

    Raises:
        builtins.MemoryError: Re-raised (treated as a system-level
            failure; not swallowed even though the rest of the function
            is best-effort).
        RecursionError: Re-raised (treated as system-level).
        MemoryError: If the related operation fails.
        CancelledError: If the related operation fails.
    """
    try:
        request = _build_distillation_request(execution_result, agent_id, task_id)
        store_request = MemoryStoreRequest(
            category=MemoryCategory.EPISODIC,
            content=_render_store_content(request),
            metadata=MemoryMetadata(
                source="distillation",
                tags=(DISTILLATION_TAG,),
            ),
        )
        await backend.store(agent_id, store_request)
    except MemoryError, RecursionError:
        logger.error(
            DISTILLATION_CAPTURE_FAILED,
            agent_id=agent_id,
            task_id=task_id,
            error_type="system",
        )
        raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            DISTILLATION_CAPTURE_FAILED,
            agent_id=agent_id,
            task_id=task_id,
            error=safe_error_description(exc),
            error_type=type(exc).__name__,
        )
        return None
    logger.info(
        DISTILLATION_CAPTURED,
        agent_id=agent_id,
        task_id=task_id,
        turns=len(execution_result.turns),
        tool_invocation_count=len(request.memory_tool_invocations),
    )
    return request
