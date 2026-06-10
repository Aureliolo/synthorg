"""Shared fixtures and helpers for workflow tests."""

import pytest

from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from synthorg.engine.workflow.enums import WorkflowEdgeType, WorkflowNodeType
from synthorg.engine.workflow.kanban_board import KanbanConfig, KanbanWipLimit
from synthorg.engine.workflow.kanban_columns import KanbanColumn
from tests._shared import coerce_id

# Re-export all helpers for direct import by test modules
__all__ = [
    "make_assignment_node",
    "make_conditional_node",
    "make_edge",
    "make_end_node",
    "make_join_node",
    "make_minimal_definition",
    "make_node",
    "make_split_node",
    "make_start_node",
    "make_task_node",
    "make_task_node_full",
    "make_verification_node",
    "make_workflow",
]

# ── Workflow graph helpers ────────────────────────────────────────


def make_node(
    node_id: str,
    node_type: WorkflowNodeType,
    label: str = "Node",
    **config: object,
) -> WorkflowNode:
    """Build a workflow node with optional config kwargs."""
    return WorkflowNode(id=node_id, type=node_type, label=label, config=config)


def make_start_node(node_id: str = "start-1") -> WorkflowNode:
    """Build a START node."""
    return WorkflowNode(id=node_id, type=WorkflowNodeType.START, label="Start")


def make_end_node(node_id: str = "end-1") -> WorkflowNode:
    """Build an END node."""
    return WorkflowNode(id=node_id, type=WorkflowNodeType.END, label="End")


def make_task_node(
    node_id: str = "task-1",
    label: str = "Do work",
) -> WorkflowNode:
    """Build a TASK node with default position and config."""
    return WorkflowNode(
        id=node_id,
        type=WorkflowNodeType.TASK,
        label=label,
        position_x=100.0,
        position_y=200.0,
        config={"task_type": "development", "priority": "high"},
    )


def make_edge(
    edge_id: str,
    source: str,
    target: str,
    edge_type: WorkflowEdgeType = WorkflowEdgeType.SEQUENTIAL,
) -> WorkflowEdge:
    """Build a workflow edge."""
    return WorkflowEdge(
        id=edge_id,
        source_node_id=source,
        target_node_id=target,
        type=edge_type,
    )


def make_workflow(
    nodes: tuple[WorkflowNode, ...],
    edges: tuple[WorkflowEdge, ...],
    **kwargs: object,
) -> WorkflowDefinition:
    """Build a WorkflowDefinition with sensible defaults.

    Any keyword argument overrides the defaults (id, name, created_by).
    """
    defaults: dict[str, object] = {
        "id": "wf-test",
        "name": "Test Workflow",
        "created_by": "test",
    }
    defaults.update(kwargs)
    defaults["id"] = coerce_id(defaults["id"])
    return WorkflowDefinition.model_validate(
        {"nodes": nodes, "edges": edges, **defaults},
    )


def make_minimal_definition(**overrides: object) -> WorkflowDefinition:
    """Build a minimal valid definition (start -> task -> end)."""
    defaults: dict[str, object] = {
        "id": "wf-1",
        "name": "Test Workflow",
        "created_by": "test-user",
        "nodes": (make_start_node(), make_task_node(), make_end_node()),
        "edges": (
            make_edge("e1", "start-1", "task-1"),
            make_edge("e2", "task-1", "end-1"),
        ),
    }
    defaults.update(overrides)
    defaults["id"] = coerce_id(defaults["id"])
    return WorkflowDefinition.model_validate(defaults)


def make_task_node_full(
    node_id: str = "task-1",
    label: str = "Do work",
    *,
    config: dict[str, str] | None = None,
) -> WorkflowNode:
    """Build a TASK node with full config for execution tests.

    Args:
        node_id: Node identifier.
        label: Human-readable label.
        config: Task config overrides (title, task_type, priority,
            complexity).  Defaults are applied for missing keys.
    """
    defaults = {
        "title": "Test Task",
        "task_type": "development",
        "priority": "high",
        "complexity": "medium",
    }
    if config:
        defaults.update(config)
    return WorkflowNode(
        id=node_id,
        type=WorkflowNodeType.TASK,
        label=label,
        position_x=100.0,
        position_y=200.0,
        config=defaults,
    )


def make_conditional_node(
    node_id: str = "cond-1",
    condition_expression: str = "true",
) -> WorkflowNode:
    """Build a CONDITIONAL node."""
    return WorkflowNode(
        id=node_id,
        type=WorkflowNodeType.CONDITIONAL,
        label="Branch",
        config={"condition_expression": condition_expression},
    )


def make_split_node(node_id: str = "split-1") -> WorkflowNode:
    """Build a PARALLEL_SPLIT node."""
    return WorkflowNode(
        id=node_id,
        type=WorkflowNodeType.PARALLEL_SPLIT,
        label="Fork",
    )


def make_join_node(node_id: str = "join-1") -> WorkflowNode:
    """Build a PARALLEL_JOIN node."""
    return WorkflowNode(
        id=node_id,
        type=WorkflowNodeType.PARALLEL_JOIN,
        label="Merge",
        config={"join_strategy": "all"},
    )


def make_assignment_node(
    node_id: str = "assign-1",
    agent_name: str = "agent-1",
) -> WorkflowNode:
    """Build an AGENT_ASSIGNMENT node."""
    return WorkflowNode(
        id=node_id,
        type=WorkflowNodeType.AGENT_ASSIGNMENT,
        label="Assign",
        config={"agent_name": agent_name},
    )


def make_verification_node(
    node_id: str = "verify-1",
    rubric_name: str = "test-rubric",
    evaluator_agent_id: str = "eval-agent",
) -> WorkflowNode:
    """Build a VERIFICATION node."""
    return WorkflowNode(
        id=node_id,
        type=WorkflowNodeType.VERIFICATION,
        label="Verify",
        config={
            "rubric_name": rubric_name,
            "evaluator_agent_id": evaluator_agent_id,
        },
    )


# ── Kanban fixtures ──────────────────────────────────────────────


@pytest.fixture
def strict_kanban_config() -> KanbanConfig:
    """KanbanConfig with tight WIP limits for testing."""
    return KanbanConfig(
        wip_limits=(
            KanbanWipLimit(column=KanbanColumn.IN_PROGRESS, limit=2),
            KanbanWipLimit(column=KanbanColumn.REVIEW, limit=1),
        ),
        enforce_wip=True,
    )


@pytest.fixture
def advisory_kanban_config() -> KanbanConfig:
    """KanbanConfig with advisory (non-enforcing) WIP limits."""
    return KanbanConfig(
        wip_limits=(
            KanbanWipLimit(column=KanbanColumn.IN_PROGRESS, limit=2),
            KanbanWipLimit(column=KanbanColumn.REVIEW, limit=1),
        ),
        enforce_wip=False,
    )
