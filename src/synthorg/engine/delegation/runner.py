# module-kind: service
"""In-process blocking sub-agent runner.

Reuses the boot :class:`~synthorg.engine.agent_engine.AgentEngine` to run
a child agent to completion inline, so the child inherits budget,
compaction, stakes routing, the ``NO_OP`` invariant, and checkpointing
for free. The child is a real persisted :class:`Task` (created and
assigned via the :class:`~synthorg.engine.task_engine.TaskEngine`), giving
the delegation an audit row and a resume point; running it inline (rather
than handing it to a worker) means no worker slot is consumed and there
is no claim/await deadlock. Child cost accrues under the parent's active
cost scope because the nested ``run`` executes inside it.

Before dispatching, the runner walks the child's parent-task chain to
bound the delegation depth and reject a cycle (the target already appears
as an ancestor's assignee), so a chain of agents delegating to one
another cannot recurse without limit.
"""

import asyncio
from collections.abc import Sequence
from typing import Final

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.delegation.errors import (
    SubAgentDelegationDepthExceededError,
    SubAgentDelegationTargetNotFoundError,
)
from synthorg.engine.delegation.models import (
    SubAgentDelegationResult,
    SubAgentDelegationSpec,
)
from synthorg.engine.run_result import AgentRunResult
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import CreateTaskData
from synthorg.hr.registry_protocol import AgentRegistryProtocol
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_DELEGATION_CHILD_CANCELLED,
    EXECUTION_DELEGATION_CHILD_COMPLETED,
    EXECUTION_DELEGATION_STARTED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

logger = get_logger(__name__)

_CHILD_TASK_TYPE: Final[TaskType] = TaskType.RESEARCH
"""Delegated sub-tasks are focused investigations whose transcript the
supervisor consumes; ``RESEARCH`` is the natural catalogue fit."""

_MAX_TRANSCRIPT_MESSAGES: Final[int] = 20
"""Cap on how many trailing child messages the digest includes."""

_MAX_MESSAGE_CHARS: Final[int] = 500
"""Per-message truncation length in the digest."""

_MAX_TRANSCRIPT_CHARS: Final[int] = 4000
"""Overall digest length cap handed back to the supervisor."""

_TRUNCATION_MARKER: Final[str] = "..."

_DEFAULT_MAX_DEPTH: Final[int] = 5
"""Fallback chain-depth cap for direct callers; the tool passes the
resolved ``engine.delegation_max_depth`` setting explicitly."""


class InProcessSubAgentRunner:
    """Run a child agent to completion via the shared ``AgentEngine``.

    Implements :class:`SubAgentRunner`.

    Args:
        engine: The boot :class:`AgentEngine` the child run is dispatched
            on (the same instance the supervisor runs on; ``AgentEngine.run``
            holds no per-run instance state, so the nested call is
            re-entrant).
        task_engine: Single-writer task actor used to create, assign, and
            (on abort) cancel the child task, and to walk the parent chain.
        agent_registry: Identity lookup for resolving the delegation
            target by id or name.
    """

    __slots__ = ("_agent_registry", "_engine", "_task_engine")

    def __init__(
        self,
        *,
        engine: AgentEngine,
        task_engine: TaskEngine,
        agent_registry: AgentRegistryProtocol,
    ) -> None:
        self._engine = engine
        self._task_engine = task_engine
        self._agent_registry = agent_registry

    async def run(
        self,
        spec: SubAgentDelegationSpec,
        *,
        max_turns: int,
        max_depth: int = _DEFAULT_MAX_DEPTH,
        timeout_seconds: float | None = None,
    ) -> SubAgentDelegationResult:
        """Resolve the target, run it on a child task, return the outcome.

        Returns:
            The bounded :class:`SubAgentDelegationResult` for the child run.

        Raises:
            SubAgentDelegationTargetNotFoundError: When ``spec.target``
                resolves to no registered agent.
            SubAgentDelegationDepthExceededError: When the delegation chain
                is already at ``max_depth`` or the target would form a cycle.
        """
        identity = await self._resolve_target(spec.target)
        await self._guard_delegation_chain(spec, identity, max_depth=max_depth)
        child_task = await self._create_child_task(
            spec,
            assignee_id=str(identity.id),
        )
        logger.info(
            EXECUTION_DELEGATION_STARTED,
            parent_task_id=spec.parent_task_id,
            requested_by=spec.requested_by,
            child_task_id=str(child_task.id),
            target_agent_id=str(identity.id),
            max_turns=max_turns,
        )
        result = await self._run_child(
            identity,
            child_task,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
        )
        delegation_result = self._to_result(identity, child_task, result)
        logger.info(
            EXECUTION_DELEGATION_CHILD_COMPLETED,
            parent_task_id=spec.parent_task_id,
            child_task_id=delegation_result.child_task_id,
            child_execution_id=delegation_result.child_execution_id,
            termination_reason=delegation_result.termination_reason.value,
            is_success=delegation_result.is_success,
            total_turns=delegation_result.total_turns,
        )
        return delegation_result

    async def _run_child(
        self,
        identity: AgentIdentity,
        child_task: Task,
        *,
        max_turns: int,
        timeout_seconds: float | None,
    ) -> AgentRunResult:
        """Dispatch the child run, cancelling the child task on abort.

        A sibling tool call failing in the supervisor's concurrent tool
        batch cancels this task mid-flight; without cleanup the child task
        would be orphaned in ``ASSIGNED`` with no terminal transition. On
        ``CancelledError`` the child task is cancelled (best-effort) and the
        cancellation is re-raised so structured concurrency still unwinds.

        Returns:
            The child :class:`AgentRunResult`.

        Raises:
            asyncio.CancelledError: Re-raised after the child task is
                cancelled, so structured concurrency still unwinds.
        """
        try:
            return await self._engine.run(
                identity=identity,
                task=child_task,
                max_turns=max_turns,
                timeout_seconds=timeout_seconds,
            )
        except asyncio.CancelledError:
            await self._cancel_child_task(child_task, requested_by=str(identity.id))
            raise

    async def _cancel_child_task(self, child_task: Task, *, requested_by: str) -> None:
        """Best-effort terminal transition for an aborted child task."""
        try:
            await self._task_engine.cancel_task(
                str(child_task.id),
                requested_by=requested_by,
                reason="delegation_cancelled",
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort cleanup on an already-
            # cancelled run; never mask the original CancelledError.
            reraise_critical(exc)
            logger.warning(
                EXECUTION_DELEGATION_CHILD_CANCELLED,
                child_task_id=str(child_task.id),
                note="failed to mark cancelled child task terminal",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        else:
            logger.info(
                EXECUTION_DELEGATION_CHILD_CANCELLED,
                child_task_id=str(child_task.id),
            )

    async def _resolve_target(self, target: str) -> AgentIdentity:
        """Resolve ``target`` to an identity by id, then by name.

        Returns:
            The resolved :class:`AgentIdentity`.

        Raises:
            SubAgentDelegationTargetNotFoundError: When neither lookup matches.
        """
        identity = await self._agent_registry.get(target)
        if identity is None:
            identity = await self._agent_registry.get_by_name(target)
        if identity is None:
            raise SubAgentDelegationTargetNotFoundError(target=target)
        return identity

    async def _guard_delegation_chain(
        self,
        spec: SubAgentDelegationSpec,
        identity: AgentIdentity,
        *,
        max_depth: int,
    ) -> None:
        """Reject a too-deep or cyclic delegation before dispatching.

        Raises:
            SubAgentDelegationDepthExceededError: When the parent-task
                chain is already ``max_depth`` deep, or the target agent
                already appears as an ancestor's assignee (a cycle, which
                includes an agent delegating to itself).
        """
        depth, ancestor_assignees = await self._chain_depth_and_assignees(
            spec.parent_task_id,
            max_depth=max_depth,
        )
        is_cycle = str(identity.id) in ancestor_assignees
        if depth >= max_depth or is_cycle:
            # The tool logs the surfaced failure; raising here keeps the guard
            # a pure precondition (no duplicate log line).
            raise SubAgentDelegationDepthExceededError(
                depth=depth,
                max_depth=max_depth,
            )

    async def _chain_depth_and_assignees(
        self,
        parent_task_id: str,
        *,
        max_depth: int,
    ) -> tuple[int, frozenset[str]]:
        """Walk the parent-task chain, returning its depth and assignees.

        The walk is hard-bounded by ``max_depth + 1`` iterations so a
        corrupt cyclic ``parent_task_id`` link cannot loop forever.

        Returns:
            ``(depth, assignees)``: the number of ancestor tasks reachable
            via ``parent_task_id`` and the set of their ``assigned_to`` ids.
        """
        assignees: set[str] = set()
        depth = 0
        current_id: str | None = parent_task_id
        for _ in range(max_depth + 1):
            if current_id is None:
                break
            task = await self._task_engine.get_task(current_id)
            if task is None:
                break
            depth += 1
            if task.assigned_to:
                assignees.add(task.assigned_to)
            current_id = task.parent_task_id
        return depth, frozenset(assignees)

    async def _create_child_task(
        self,
        spec: SubAgentDelegationSpec,
        *,
        assignee_id: str,
    ) -> Task:
        """Create and assign the persisted child task.

        Returns:
            The child :class:`Task` in ``ASSIGNED`` state, ready to run.
        """
        data = CreateTaskData(
            title=spec.title,
            description=spec.description,
            type=_CHILD_TASK_TYPE,
            project=spec.project,
            created_by=spec.requested_by,
        )
        task = await self._task_engine.create_task(
            data,
            requested_by=spec.requested_by,
        )
        assigned, _prior = await self._task_engine.transition_task(
            str(task.id),
            TaskStatus.ASSIGNED,
            requested_by=spec.requested_by,
            reason="delegate_and_await",
            assigned_to=assignee_id,
            parent_task_id=spec.parent_task_id,
        )
        return assigned

    def _to_result(
        self,
        identity: AgentIdentity,
        child_task: Task,
        result: AgentRunResult,
    ) -> SubAgentDelegationResult:
        """Fold the child run into a bounded delegation result.

        Returns:
            The delegation result carrying the child's answer, a bounded
            transcript digest, and cost / turn accounting.
        """
        return SubAgentDelegationResult(
            child_task_id=str(child_task.id),
            child_execution_id=result.execution_result.context.execution_id,
            target_agent_id=str(identity.id),
            termination_reason=result.termination_reason,
            final_answer=result.completion_summary,
            transcript_summary=_summarise_transcript(
                result.execution_result.context.conversation,
            ),
            total_cost=result.total_cost,
            currency=result.currency,
            total_turns=result.total_turns,
        )


def _summarise_transcript(conversation: Sequence[ChatMessage]) -> str:
    """Build a bounded, human-readable digest of a child conversation.

    Skips the system prompt, keeps the trailing
    :data:`_MAX_TRANSCRIPT_MESSAGES` user / assistant / tool messages,
    truncates each to :data:`_MAX_MESSAGE_CHARS`, and caps the whole
    digest at :data:`_MAX_TRANSCRIPT_CHARS`.

    Returns:
        The digest string (empty when the child produced no non-system
        message content).
    """
    lines: list[str] = []
    considered = [
        message
        for message in conversation
        if message.role != MessageRole.SYSTEM and message.content
    ]
    for message in considered[-_MAX_TRANSCRIPT_MESSAGES:]:
        content = message.content or ""
        if len(content) > _MAX_MESSAGE_CHARS:
            content = content[:_MAX_MESSAGE_CHARS] + _TRUNCATION_MARKER
        lines.append(f"{message.role.value}: {content}")
    digest = "\n".join(lines)
    if len(digest) > _MAX_TRANSCRIPT_CHARS:
        digest = digest[:_MAX_TRANSCRIPT_CHARS] + _TRUNCATION_MARKER
    return digest
