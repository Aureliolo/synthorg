"""Per-`WorkflowNodeType` step builders dispatched through a registry.

Each builder mutates the partially-assembled YAML step dict in place,
adding type-specific fields.  The registry covers every
:class:`~synthorg.core.enums.WorkflowNodeType` member that reaches the
builder pipeline; ``START`` and ``END`` are filtered upstream in
``yaml_export._generate_steps`` and are intentionally absent from the
registry so a stray START/END node would surface as a ``KeyError``.
"""

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any, Final

from synthorg.core.enums import WorkflowEdgeType, WorkflowNodeType

_TASK_CONFIG_KEYS: Final = (
    "title",
    "task_type",
    "priority",
    "complexity",
    "coordination_topology",
)
_ASSIGNMENT_KEYS: Final = (
    "routing_strategy",
    "role_filter",
    "agent_name",
)
_ASSIGNMENT_STEP_MAP: Final[Mapping[str, str]] = MappingProxyType(
    {
        "routing_strategy": "strategy",
        "role_filter": "role",
        "agent_name": "agent_name",
    },
)


type StepBuilder = Callable[
    [dict[str, Any], dict[str, Any], list[tuple[str, WorkflowEdgeType]]],
    None,
]


def _build_task(
    step: dict[str, Any],
    config: dict[str, Any],
    outgoing_edges: list[tuple[str, WorkflowEdgeType]],
) -> None:
    """Copy task config fields into the step, plus an optional embedded assignment."""
    del outgoing_edges
    for key in _TASK_CONFIG_KEYS:
        if key in config:
            step[key] = config[key]
    if "routing_strategy" in config or "role_filter" in config:
        assignment = {
            _ASSIGNMENT_STEP_MAP[k]: str(config[k])
            for k in _ASSIGNMENT_KEYS
            if k in config
        }
        step["agent_assignment"] = assignment


def _build_assignment(
    step: dict[str, Any],
    config: dict[str, Any],
    outgoing_edges: list[tuple[str, WorkflowEdgeType]],
) -> None:
    """Copy agent assignment fields into the step, remapped via the step map."""
    del outgoing_edges
    for key in _ASSIGNMENT_KEYS:
        if key in config:
            step[_ASSIGNMENT_STEP_MAP[key]] = config[key]


def _build_conditional(
    step: dict[str, Any],
    config: dict[str, Any],
    outgoing_edges: list[tuple[str, WorkflowEdgeType]],
) -> None:
    """Add the conditional expression as a top-level ``condition`` field."""
    del outgoing_edges
    if "condition_expression" in config:
        step["condition"] = config["condition_expression"]


def _build_parallel_split(
    step: dict[str, Any],
    config: dict[str, Any],
    outgoing_edges: list[tuple[str, WorkflowEdgeType]],
) -> None:
    """Emit branch targets and optional ``max_concurrency`` for a split node."""
    step["branches"] = [
        target
        for target, edge_type in outgoing_edges
        if edge_type == WorkflowEdgeType.PARALLEL_BRANCH
    ]
    if "max_concurrency" in config:
        step["max_concurrency"] = config["max_concurrency"]


def _build_parallel_join(
    step: dict[str, Any],
    config: dict[str, Any],
    outgoing_edges: list[tuple[str, WorkflowEdgeType]],
) -> None:
    """Emit ``join_strategy`` defaulting to ``"all"``."""
    del outgoing_edges
    step["join_strategy"] = config.get("join_strategy", "all")


def _build_subworkflow(
    step: dict[str, Any],
    config: dict[str, Any],
    outgoing_edges: list[tuple[str, WorkflowEdgeType]],
) -> None:
    """Copy the subworkflow reference and optional binding maps."""
    del outgoing_edges
    if "subworkflow_id" in config:
        step["subworkflow_id"] = config["subworkflow_id"]
    if "version" in config:
        step["version"] = config["version"]
    if config.get("input_bindings"):
        step["input_bindings"] = dict(config["input_bindings"])
    if config.get("output_bindings"):
        step["output_bindings"] = dict(config["output_bindings"])


def _build_verification(
    step: dict[str, Any],
    config: dict[str, Any],
    outgoing_edges: list[tuple[str, WorkflowEdgeType]],
) -> None:
    """No-op: verification nodes carry no extra fields in the YAML output."""
    del step, config, outgoing_edges


STEP_BUILDERS: Final[Mapping[WorkflowNodeType, StepBuilder]] = MappingProxyType(
    {
        WorkflowNodeType.TASK: _build_task,
        WorkflowNodeType.AGENT_ASSIGNMENT: _build_assignment,
        WorkflowNodeType.CONDITIONAL: _build_conditional,
        WorkflowNodeType.PARALLEL_SPLIT: _build_parallel_split,
        WorkflowNodeType.PARALLEL_JOIN: _build_parallel_join,
        WorkflowNodeType.SUBWORKFLOW: _build_subworkflow,
        WorkflowNodeType.VERIFICATION: _build_verification,
    },
)
