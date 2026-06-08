"""Activation-time helper functions for workflow execution.

Graph-walking, conditional node processing, and task config
parsing used by ``WorkflowExecutionService.activate()``.
"""

from collections import deque
from typing import TYPE_CHECKING

from synthorg.core.enums import Complexity, Priority, TaskType
from synthorg.engine.errors import WorkflowConditionEvalError
from synthorg.engine.quality.verification import VerificationVerdict
from synthorg.engine.task_engine_models import CreateTaskData
from synthorg.engine.workflow.condition_eval import evaluate_condition
from synthorg.engine.workflow.enums import (
    WorkflowEdgeType,
    WorkflowNodeExecutionStatus,
    WorkflowNodeType,
)
from synthorg.engine.workflow.execution_models import WorkflowNodeExecution
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.verification import (
    VERIFICATION_VERDICT_ROUTED,
)
from synthorg.observability.events.workflow_execution import (
    WORKFLOW_EXEC_CONDITION_EVAL_FAILED,
    WORKFLOW_EXEC_CONDITION_EVALUATED,
    WORKFLOW_EXEC_TASK_CREATED,
)

if TYPE_CHECKING:
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.engine.workflow.definition import WorkflowNode

logger = get_logger(__name__)

_TASK_TYPE_MAP: dict[str, TaskType] = {t.value: t for t in TaskType}
_PRIORITY_MAP: dict[str, Priority] = {p.value: p for p in Priority}
_COMPLEXITY_MAP: dict[str, Complexity] = {c.value: c for c in Complexity}

# Node types that produce no concrete task (control flow and metadata)
CONTROL_NODE_TYPES = frozenset(
    {
        WorkflowNodeType.START,
        WorkflowNodeType.END,
        WorkflowNodeType.AGENT_ASSIGNMENT,
        WorkflowNodeType.CONDITIONAL,
        WorkflowNodeType.PARALLEL_SPLIT,
        WorkflowNodeType.PARALLEL_JOIN,
        WorkflowNodeType.VERIFICATION,
    }
)


def find_downstream_task_ids(
    nid: str,
    adjacency: dict[str, list[str]],
    node_map: dict[str, WorkflowNode],
) -> list[str]:
    """Walk forward through control nodes to find downstream TASK nodes.

    Used by AGENT_ASSIGNMENT to propagate assignments through
    intervening control nodes (CONDITIONAL, SPLIT, JOIN, etc.)
    to reach the actual TASK nodes.  Stops at other
    AGENT_ASSIGNMENT nodes (which override).

    Returns:
        List of TASK node ids reachable from ``nid`` via control
        nodes; the search stops at downstream AGENT_ASSIGNMENT
        nodes (those override the upstream assignment).
    """
    result: list[str] = []
    visited: set[str] = set()
    queue: deque[str] = deque(adjacency.get(nid, []))
    while queue:
        tid = queue.popleft()
        if tid in visited:
            continue
        visited.add(tid)
        target_node = node_map[tid]
        if target_node.type is WorkflowNodeType.TASK:
            result.append(tid)
        elif target_node.type is WorkflowNodeType.AGENT_ASSIGNMENT:
            pass  # Another assignment overrides -- stop propagation
        elif target_node.type in CONTROL_NODE_TYPES:
            queue.extend(adjacency.get(tid, []))
    return result


def _flatten_task_ids(
    value: str | tuple[str, ...],
    out: list[str],
) -> None:
    """Append task ID(s) from a node_task_ids entry to *out*."""
    if isinstance(value, str):
        out.append(value)
    else:
        out.extend(value)


def find_upstream_task_ids(
    node_id: str,
    reverse_adj: dict[str, list[str]],
    node_map: dict[str, WorkflowNode],
    node_task_ids: dict[str, str | tuple[str, ...]],
    skipped: set[str],
) -> tuple[str, ...]:
    """Walk backwards to find the nearest upstream TASK/SUBWORKFLOW IDs.

    Skips through control nodes (START, END, AGENT_ASSIGNMENT,
    CONDITIONAL, PARALLEL_SPLIT, PARALLEL_JOIN) to find the actual
    TASK and SUBWORKFLOW predecessors.  Skipped nodes are excluded.
    TASK and SUBWORKFLOW nodes that have not yet produced a task ID
    (not in ``node_task_ids``) are also excluded.  SUBWORKFLOW
    entries may contain a tuple of terminal task IDs from the child
    graph.

    Returns:
        Sorted tuple of unique upstream task ids reachable from
        ``node_id`` via the control-node hops described above.
    """
    result: list[str] = []
    visited: set[str] = set()
    queue: deque[str] = deque(reverse_adj.get(node_id, []))

    while queue:
        pred_id = queue.popleft()
        if pred_id in visited or pred_id in skipped:
            continue
        visited.add(pred_id)
        pred_node = node_map[pred_id]
        if pred_node.type in {
            WorkflowNodeType.TASK,
            WorkflowNodeType.SUBWORKFLOW,
        }:
            if pred_id in node_task_ids:
                _flatten_task_ids(node_task_ids[pred_id], result)
            else:
                # Subworkflow produced no tasks -- treat as a
                # control hop and keep walking backwards.
                queue.extend(reverse_adj.get(pred_id, []))
        elif pred_node.type in CONTROL_NODE_TYPES:
            # Keep walking backwards through control nodes
            queue.extend(reverse_adj.get(pred_id, []))

    return tuple(sorted(set(result)))


def find_skipped_nodes(
    untaken_target: str,
    taken_target: str,
    adjacency: dict[str, list[str]],
) -> set[str]:
    """Find nodes reachable only through the untaken branch.

    BFS from the untaken target, collecting all downstream nodes
    that are NOT reachable from the taken target.

    Returns:
        Set of node ids reachable from ``untaken_target`` but not
        from ``taken_target`` (i.e. the skipped-branch frontier).
    """
    # Find all nodes reachable from the taken branch
    taken_reachable: set[str] = set()
    queue: deque[str] = deque([taken_target])
    while queue:
        nid = queue.popleft()
        if nid in taken_reachable:
            continue
        taken_reachable.add(nid)
        queue.extend(adjacency.get(nid, []))

    # BFS from untaken target, skip nodes in taken_reachable
    skipped: set[str] = set()
    queue = deque([untaken_target])
    while queue:
        nid = queue.popleft()
        if nid in skipped or nid in taken_reachable:
            continue
        skipped.add(nid)
        queue.extend(adjacency.get(nid, []))

    return skipped


def process_conditional_node(  # noqa: PLR0913
    nid: str,
    node: WorkflowNode,
    ctx: dict[str, object],
    outgoing: dict[str, list[tuple[str, WorkflowEdgeType]]],
    adjacency: dict[str, list[str]],
    skipped_nodes: set[str],
    execution_id: str,
) -> WorkflowNodeExecution:
    """Evaluate condition and mark untaken branch as skipped.

    Args:
        nid: The node ID being processed.
        node: The CONDITIONAL workflow node.
        ctx: Runtime context for condition evaluation.
        outgoing: Typed outgoing edge map.
        adjacency: Forward adjacency map.
        skipped_nodes: Accumulator of skipped node IDs (mutated).
        execution_id: Execution ID for logging.

    Returns:
        A completed ``WorkflowNodeExecution``.

    Raises:
        WorkflowConditionEvalError: On evaluation failure.
    """
    expr = str(node.config.get("condition_expression", "false"))
    if not node.config.get("condition_expression"):
        logger.warning(
            WORKFLOW_EXEC_CONDITION_EVALUATED,
            execution_id=execution_id,
            node_id=nid,
            expression=expr,
            result=False,
            note="missing condition_expression, defaulting to false",
        )
    try:
        result = evaluate_condition(expr, ctx)
    except (ValueError, TypeError, KeyError) as exc:
        error_desc = safe_error_description(exc)
        logger.warning(
            WORKFLOW_EXEC_CONDITION_EVAL_FAILED,
            execution_id=execution_id,
            node_id=nid,
            expression=expr,
            error_type=type(exc).__name__,
            error=error_desc,
        )
        msg = f"Failed to evaluate condition on node {nid!r}: {error_desc}"
        raise WorkflowConditionEvalError(msg) from exc

    safe_expr = expr.replace("\n", " ").replace("\r", " ")
    logger.info(
        WORKFLOW_EXEC_CONDITION_EVALUATED,
        execution_id=execution_id,
        node_id=nid,
        expression=safe_expr,
        result=result,
    )

    # Determine taken/untaken edges
    true_target: str | None = None
    false_target: str | None = None
    for target_id, edge_type in outgoing.get(nid, []):
        if edge_type is WorkflowEdgeType.CONDITIONAL_TRUE:
            true_target = target_id
        elif edge_type is WorkflowEdgeType.CONDITIONAL_FALSE:
            false_target = target_id

    if result:
        taken, untaken = true_target, false_target
    else:
        taken, untaken = false_target, true_target

    if true_target is None or false_target is None:
        logger.warning(
            WORKFLOW_EXEC_CONDITION_EVALUATED,
            execution_id=execution_id,
            node_id=nid,
            note="conditional node missing true or false edge",
            true_target=true_target,
            false_target=false_target,
        )

    if untaken is not None and taken is not None:
        skipped_nodes.update(
            find_skipped_nodes(untaken, taken, adjacency),
        )

    return WorkflowNodeExecution(
        node_id=nid,
        node_type=node.type,
        status=WorkflowNodeExecutionStatus.COMPLETED,
    )


def parse_task_config(
    config: dict[str, object],
    node: WorkflowNode,
    nid: str,
) -> tuple[str, str, TaskType, Priority, Complexity]:
    """Parse TASK node config into validated task parameters.

    Returns:
        A 5-tuple of (title, description, task_type, priority, complexity).

    Raises:
        ValueError: When ``task_type``, ``priority``, or
            ``estimated_complexity`` carry unknown literals.
    """
    title = str(config.get("title", node.label))
    description = str(config.get("description", f"Task from workflow node {nid}"))

    raw_type = str(config.get("task_type", "development"))
    if raw_type not in _TASK_TYPE_MAP:
        msg = (
            f"Node {nid!r} has unrecognized task_type {raw_type!r}"
            f" (valid: {sorted(_TASK_TYPE_MAP)})"
        )
        logger.error(
            WORKFLOW_EXEC_TASK_CREATED,
            node_id=nid,
            error=msg,
        )
        raise ValueError(msg)
    task_type = _TASK_TYPE_MAP[raw_type]

    raw_priority = str(config.get("priority", "medium"))
    if raw_priority not in _PRIORITY_MAP:
        msg = (
            f"Node {nid!r} has unrecognized priority {raw_priority!r}"
            f" (valid: {sorted(_PRIORITY_MAP)})"
        )
        logger.error(
            WORKFLOW_EXEC_TASK_CREATED,
            node_id=nid,
            error=msg,
        )
        raise ValueError(msg)
    priority = _PRIORITY_MAP[raw_priority]

    raw_complexity = str(config.get("complexity", "medium"))
    if raw_complexity not in _COMPLEXITY_MAP:
        msg = (
            f"Node {nid!r} has unrecognized complexity {raw_complexity!r}"
            f" (valid: {sorted(_COMPLEXITY_MAP)})"
        )
        logger.error(
            WORKFLOW_EXEC_TASK_CREATED,
            node_id=nid,
            error=msg,
        )
        raise ValueError(msg)
    complexity = _COMPLEXITY_MAP[raw_complexity]

    return title, description, task_type, priority, complexity


async def process_task_node(  # noqa: PLR0913
    nid: str,
    node: WorkflowNode,
    *,
    reverse_adj: dict[str, list[str]],
    node_map: dict[str, WorkflowNode],
    node_task_ids: dict[str, str | tuple[str, ...]],
    skipped_nodes: set[str],
    pending_assignments: dict[str, str],
    project: str,
    activated_by: str,
    task_engine: TaskEngine,
    execution_id: str,
) -> WorkflowNodeExecution:
    """Create a concrete task for a TASK node.

    Args:
        nid: The node ID being processed.
        node: The TASK workflow node.
        reverse_adj: Reverse adjacency map.
        node_map: All nodes keyed by ID.
        node_task_ids: Accumulator of node-to-task mappings (mutated).
        skipped_nodes: Set of skipped node IDs.
        pending_assignments: Agent assignments (mutated via pop).
        project: Project ID for the created task.
        activated_by: Identity of the activating user.
        task_engine: Engine for creating tasks.
        execution_id: Execution ID for logging.

    Returns:
        A ``WorkflowNodeExecution`` in TASK_CREATED status.
    """
    config = dict(node.config)
    title, description, task_type, priority, complexity = parse_task_config(
        config,
        node,
        nid,
    )
    assigned_to = pending_assignments.pop(nid, None)
    upstream_ids = find_upstream_task_ids(
        nid,
        reverse_adj,
        node_map,
        node_task_ids,
        skipped_nodes,
    )

    task_data = CreateTaskData(
        title=title,
        description=description,
        type=task_type,
        priority=priority,
        project=project,
        created_by=activated_by,
        assigned_to=assigned_to,
        dependencies=upstream_ids,
        estimated_complexity=complexity,
    )

    task = await task_engine.create_task(
        task_data,
        requested_by="workflow-engine",
    )

    node_task_ids[nid] = str(task.id)
    logger.info(
        WORKFLOW_EXEC_TASK_CREATED,
        execution_id=execution_id,
        node_id=nid,
        task_id=str(task.id),
        title=title,
    )

    return WorkflowNodeExecution(
        node_id=nid,
        node_type=node.type,
        status=WorkflowNodeExecutionStatus.TASK_CREATED,
        task_id=str(task.id),
    )


def process_verification_node(  # noqa: PLR0913
    nid: str,
    node: WorkflowNode,
    outgoing: dict[str, list[tuple[str, WorkflowEdgeType]]],
    adjacency: dict[str, list[str]],
    skipped_nodes: set[str],
    execution_id: str,
    verdict: VerificationVerdict,
) -> WorkflowNodeExecution:
    """Route a verification node based on a grader verdict.

    Selects the outgoing edge matching *verdict*, marks the other
    two branches as skipped via ``find_skipped_nodes``.

    Args:
        nid: Node identifier.
        node: The verification node.
        outgoing: Adjacency map of (target_id, edge_type) tuples.
        adjacency: Full graph adjacency for skipped-node BFS.
        skipped_nodes: Mutable set updated with skipped branch nodes.
        execution_id: Execution identifier for logging.
        verdict: The verification verdict (PASS/FAIL/REFER).

    Returns:
        A completed node execution.
    """
    edge_map: dict[VerificationVerdict, str | None] = {
        VerificationVerdict.PASS: None,
        VerificationVerdict.FAIL: None,
        VerificationVerdict.REFER: None,
    }
    verdict_to_edge = {
        WorkflowEdgeType.VERIFICATION_PASS: VerificationVerdict.PASS,
        WorkflowEdgeType.VERIFICATION_FAIL: VerificationVerdict.FAIL,
        WorkflowEdgeType.VERIFICATION_REFER: VerificationVerdict.REFER,
    }
    for target_id, edge_type in outgoing.get(nid, []):
        v = verdict_to_edge.get(edge_type)
        if v is not None:
            edge_map[v] = target_id

    taken_target = edge_map.get(verdict)
    untaken_targets = [t for v, t in edge_map.items() if v != verdict and t is not None]

    logger.info(
        VERIFICATION_VERDICT_ROUTED,
        execution_id=execution_id,
        node_id=nid,
        verdict=verdict.value,
        taken_target=taken_target,
    )

    if taken_target is None:
        logger.warning(
            VERIFICATION_VERDICT_ROUTED,
            execution_id=execution_id,
            node_id=nid,
            note=f"verification node missing {verdict.value} edge",
        )

    if taken_target is not None:
        for untaken in untaken_targets:
            skipped_nodes.update(
                find_skipped_nodes(untaken, taken_target, adjacency),
            )

    return WorkflowNodeExecution(
        node_id=nid,
        node_type=node.type,
        status=WorkflowNodeExecutionStatus.COMPLETED,
    )
