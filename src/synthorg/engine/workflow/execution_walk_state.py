"""Walk-state helpers for workflow execution activation.

Shared mutable accumulators (``_WalkState``), ID qualification,
and terminal-task-id collection used by
``WorkflowExecutionService.activate``.
"""

from dataclasses import dataclass, field

from synthorg.engine.workflow.definition import WorkflowDefinition
from synthorg.engine.workflow.enums import WorkflowNodeType
from synthorg.engine.workflow.execution_models import WorkflowNodeExecution
from synthorg.engine.workflow.graph_utils import build_adjacency_maps

QUALIFIED_ID_SEPARATOR = "::"


def qualify_id(prefix: str, node_id: str) -> str:
    """Build a qualified node ID ``{prefix}::{node_id}`` or return *node_id*.

    When *prefix* is empty the node ID is returned unchanged so that
    top-level graphs keep their existing unqualified IDs.

    Returns:
        ``"{prefix}::{node_id}"`` when ``prefix`` is non-empty;
        ``node_id`` unchanged otherwise.
    """
    if not prefix:
        return node_id
    return f"{prefix}{QUALIFIED_ID_SEPARATOR}{node_id}"


@dataclass
class WalkState:
    """Mutable accumulators shared across all frames in a single activation."""

    node_exec_map: dict[str, WorkflowNodeExecution] = field(
        default_factory=dict,
    )
    node_task_ids: dict[str, str | tuple[str, ...]] = field(default_factory=dict)
    ordered_keys: list[str] = field(default_factory=list)


def collect_terminal_task_ids(
    child_definition: WorkflowDefinition,
    child_prefix: str,
    state: WalkState,
) -> tuple[str, ...]:
    """Collect task IDs from a child graph's terminal executable nodes.

    A terminal node is one whose only successors are END nodes or
    that has no successors at all.  Both TASK and SUBWORKFLOW nodes
    are considered -- SUBWORKFLOW entries may store a tuple of child
    terminal task IDs that must be flattened.

    Returns:
        A tuple of task IDs (possibly empty when the child graph
        had no executable nodes or all were skipped).
    """
    adjacency, _, _ = build_adjacency_maps(child_definition)
    node_map = {n.id: n for n in child_definition.nodes}
    terminal_task_ids: list[str] = []
    for node in child_definition.nodes:
        if node.type not in (
            WorkflowNodeType.TASK,
            WorkflowNodeType.SUBWORKFLOW,
        ):
            continue
        qualified = qualify_id(child_prefix, node.id)
        successors = adjacency.get(node.id, [])
        is_terminal = (
            all(node_map[s].type is WorkflowNodeType.END for s in successors)
            or not successors
        )
        if not is_terminal:
            continue
        if node.type is WorkflowNodeType.TASK:
            task_id = state.node_task_ids.get(qualified)
            if isinstance(task_id, str):
                terminal_task_ids.append(task_id)
        else:
            entry = state.node_task_ids.get(qualified)
            if isinstance(entry, tuple):
                terminal_task_ids.extend(entry)
            elif isinstance(entry, str):
                terminal_task_ids.append(entry)
    return tuple(terminal_task_ids)
