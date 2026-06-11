"""Per-node dispatch machinery for workflow execution.

Holds the uniform ``(service, ctx) -> WorkflowNodeExecution`` node
handlers and the ``WorkflowNodeType``-keyed registry that
``WorkflowExecutionService`` walks. Kept separate from the service so
the activation service stays focused on graph traversal and frame
management.
"""

from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from synthorg.core.registry import StrategyRegistry
from synthorg.engine.quality.verification import VerificationVerdict
from synthorg.engine.workflow.definition import WorkflowNode
from synthorg.engine.workflow.enums import (
    WorkflowEdgeType,
    WorkflowNodeExecutionStatus,
    WorkflowNodeType,
)
from synthorg.engine.workflow.execution_activation_helpers import (
    find_downstream_task_ids,
    process_conditional_node,
    process_verification_node,
)
from synthorg.engine.workflow.execution_models import (
    ExecutionFrame,
    WorkflowNodeExecution,
)
from synthorg.engine.workflow.execution_walk_state import WalkState
from synthorg.observability import get_logger
from synthorg.observability.events.workflow_execution import (
    WORKFLOW_EXEC_NODE_COMPLETED,
)

logger = get_logger(__name__)


@runtime_checkable
class _WorkflowNodeProcessor(Protocol):
    """Structural surface the SUBWORKFLOW / TASK handlers call back into.

    ``execution_service`` imports this module for its node-dispatch
    registry, so this module cannot import the concrete
    ``WorkflowExecutionService`` at runtime without closing that cycle.
    The two delegating handlers annotate against this protocol instead;
    ``WorkflowExecutionService`` satisfies it structurally, so the
    annotation resolves at runtime (typeguard) without the import.
    """

    async def _process_task_node_in_frame(  # noqa: PLR0913
        self,
        *,
        nid: str,
        qualified_id: str,
        node: WorkflowNode,
        reverse_adj: dict[str, list[str]],
        node_map: dict[str, WorkflowNode],
        frame_node_task_ids: dict[str, str | tuple[str, ...]],
        qualifier_prefix: str,
        state: WalkState,
        skipped_nodes: set[str],
        pending_assignments: dict[str, str],
        project: str,
        activated_by: str,
        execution_id: str,
    ) -> WorkflowNodeExecution:
        """Create a task for a TASK node under its frame-qualified key."""
        ...

    async def _process_subworkflow_node(  # noqa: PLR0913
        self,
        *,
        nid: str,
        qualified_id: str,
        node: WorkflowNode,
        frame: ExecutionFrame,
        frame_ctx: dict[str, object],
        frame_node_task_ids: dict[str, str | tuple[str, ...]],
        state: WalkState,
        execution_id: str,
        project: str,
        activated_by: str,
    ) -> WorkflowNodeExecution:
        """Resolve a SUBWORKFLOW node and walk the child graph in a frame."""
        ...


@dataclass(frozen=True, slots=True)
class _NodeDispatchContext:
    """Immutable bundle of the per-node dispatch arguments.

    Replaces the wide ``**kwargs`` surface the old if-cascade carried
    so every node handler has a single uniform signature
    ``(service, ctx) -> WorkflowNodeExecution``.
    """

    nid: str
    qualified_id: str
    node: WorkflowNode
    adjacency: dict[str, list[str]]
    reverse_adj: dict[str, list[str]]
    outgoing: dict[str, list[tuple[str, WorkflowEdgeType]]]
    frame: ExecutionFrame
    frame_ctx: dict[str, object]
    execution_id: str
    project: str
    activated_by: str
    node_map: dict[str, WorkflowNode]
    frame_node_task_ids: dict[str, str | tuple[str, ...]]
    qualifier_prefix: str
    state: WalkState
    skipped_nodes: set[str]
    pending_assignments: dict[str, str]


async def _handle_terminal(
    service: _WorkflowNodeProcessor,  # noqa: ARG001
    ctx: _NodeDispatchContext,
) -> WorkflowNodeExecution:
    """START / END / PARALLEL_SPLIT / PARALLEL_JOIN: complete immediately.

    Returns:
        A :class:`WorkflowNodeExecution` recording the node as
        COMPLETED; these node types have no per-node side effects.
    """
    logger.debug(
        WORKFLOW_EXEC_NODE_COMPLETED,
        execution_id=ctx.execution_id,
        node_id=ctx.qualified_id,
        node_type=ctx.node.type.value,
    )
    return WorkflowNodeExecution(
        node_id=ctx.qualified_id,
        node_type=ctx.node.type,
        status=WorkflowNodeExecutionStatus.COMPLETED,
    )


async def _handle_agent_assignment(
    service: _WorkflowNodeProcessor,  # noqa: ARG001
    ctx: _NodeDispatchContext,
) -> WorkflowNodeExecution:
    """AGENT_ASSIGNMENT: record pending assignments on downstream tasks.

    Returns:
        A COMPLETED :class:`WorkflowNodeExecution`; downstream TASK
        nodes receive the resolved agent assignment via the
        ``pending_assignments`` accumulator.
    """
    agent_name = ctx.node.config.get("agent_name")
    if agent_name:
        task_targets = find_downstream_task_ids(
            ctx.nid,
            ctx.adjacency,
            ctx.node_map,
        )
        for target_id in task_targets:
            ctx.pending_assignments[target_id] = str(agent_name)
    else:
        logger.warning(
            WORKFLOW_EXEC_NODE_COMPLETED,
            execution_id=ctx.execution_id,
            node_id=ctx.qualified_id,
            note="AGENT_ASSIGNMENT node has no agent_name",
        )
    logger.debug(
        WORKFLOW_EXEC_NODE_COMPLETED,
        execution_id=ctx.execution_id,
        node_id=ctx.qualified_id,
        node_type=ctx.node.type.value,
    )
    return WorkflowNodeExecution(
        node_id=ctx.qualified_id,
        node_type=ctx.node.type,
        status=WorkflowNodeExecutionStatus.COMPLETED,
    )


async def _handle_verification(
    service: _WorkflowNodeProcessor,  # noqa: ARG001
    ctx: _NodeDispatchContext,
) -> WorkflowNodeExecution:
    """VERIFICATION: resolve verdict, delegate, requalify node_id.

    Returns:
        The :class:`WorkflowNodeExecution` produced by
        :func:`process_verification_node`, with its ``node_id``
        replaced by the qualified id so persistence uses the
        frame-scoped identifier.
    """
    verdict_str = str(ctx.node.config.get("_verdict_override", "refer"))
    try:
        verdict = VerificationVerdict(verdict_str)
    except ValueError:
        verdict = VerificationVerdict.REFER
    verification_execution = process_verification_node(
        ctx.nid,
        ctx.node,
        ctx.outgoing,
        ctx.adjacency,
        ctx.skipped_nodes,
        ctx.execution_id,
        verdict,
    )
    return verification_execution.model_copy(
        update={"node_id": ctx.qualified_id},
    )


async def _handle_conditional(
    service: _WorkflowNodeProcessor,  # noqa: ARG001
    ctx: _NodeDispatchContext,
) -> WorkflowNodeExecution:
    """CONDITIONAL: evaluate branch, requalify node_id for stable persistence.

    Returns:
        The :class:`WorkflowNodeExecution` produced by
        :func:`process_conditional_node`, with its ``node_id``
        replaced by the qualified id.
    """
    conditional_execution = process_conditional_node(
        ctx.nid,
        ctx.node,
        ctx.frame_ctx,
        ctx.outgoing,
        ctx.adjacency,
        ctx.skipped_nodes,
        ctx.execution_id,
    )
    return conditional_execution.model_copy(
        update={"node_id": ctx.qualified_id},
    )


async def _handle_subworkflow(
    service: _WorkflowNodeProcessor,
    ctx: _NodeDispatchContext,
) -> WorkflowNodeExecution:
    """SUBWORKFLOW: push a child frame and walk it.

    Returns:
        The :class:`WorkflowNodeExecution` produced by the service's
        private subworkflow processor.
    """
    # SLF001: these handlers are the extracted bodies of what was a
    # private dispatch method on the service; they remain internal to
    # this module's node-dispatch machinery and intentionally call the
    # service's own private node processors.
    return await service._process_subworkflow_node(  # noqa: SLF001
        nid=ctx.nid,
        qualified_id=ctx.qualified_id,
        node=ctx.node,
        frame=ctx.frame,
        frame_ctx=ctx.frame_ctx,
        frame_node_task_ids=ctx.frame_node_task_ids,
        state=ctx.state,
        execution_id=ctx.execution_id,
        project=ctx.project,
        activated_by=ctx.activated_by,
    )


async def _handle_task(
    service: _WorkflowNodeProcessor,
    ctx: _NodeDispatchContext,
) -> WorkflowNodeExecution:
    """TASK: create a task for the node under its qualified key.

    Returns:
        The :class:`WorkflowNodeExecution` produced by the service's
        private task processor (records the new task id and frame).
    """
    # SLF001: see ``_handle_subworkflow`` -- same in-module dispatch
    # rationale for calling the service's private task processor.
    return await service._process_task_node_in_frame(  # noqa: SLF001
        nid=ctx.nid,
        qualified_id=ctx.qualified_id,
        node=ctx.node,
        reverse_adj=ctx.reverse_adj,
        node_map=ctx.node_map,
        frame_node_task_ids=ctx.frame_node_task_ids,
        qualifier_prefix=ctx.qualifier_prefix,
        state=ctx.state,
        skipped_nodes=ctx.skipped_nodes,
        pending_assignments=ctx.pending_assignments,
        project=ctx.project,
        activated_by=ctx.activated_by,
        execution_id=ctx.execution_id,
    )


_NODE_HANDLER_REGISTRY: StrategyRegistry[
    Coroutine[object, object, WorkflowNodeExecution]
] = StrategyRegistry(
    {
        WorkflowNodeType.START: _handle_terminal,
        WorkflowNodeType.END: _handle_terminal,
        WorkflowNodeType.PARALLEL_SPLIT: _handle_terminal,
        WorkflowNodeType.PARALLEL_JOIN: _handle_terminal,
        WorkflowNodeType.AGENT_ASSIGNMENT: _handle_agent_assignment,
        WorkflowNodeType.VERIFICATION: _handle_verification,
        WorkflowNodeType.CONDITIONAL: _handle_conditional,
        WorkflowNodeType.SUBWORKFLOW: _handle_subworkflow,
        WorkflowNodeType.TASK: _handle_task,
    },
    kind="workflow_node_handler",
)
