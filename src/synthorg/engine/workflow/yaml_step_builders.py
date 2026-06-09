"""Per-`WorkflowNodeType` step builders dispatched through a registry.

Each builder mutates ``StepBuildContext.step`` in place, adding type-specific
fields.  Wrapping the step dict, the read-only node config, and the
read-only outgoing-edge list in a frozen :class:`StepBuildContext` keeps
the handler contract self-documenting: ``step`` is the mutable output
slot, while ``config`` and ``outgoing_edges`` are read-only inputs.

The registry covers every :class:`~synthorg.engine.workflow.enums.WorkflowNodeType`
member that reaches the builder pipeline; ``START`` and ``END`` are
filtered upstream in ``yaml_export._generate_steps`` and are intentionally
absent from the registry so a stray START/END node would surface as a
``KeyError``.
"""

import copy
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, cast

from synthorg.engine.workflow.enums import WorkflowEdgeType, WorkflowNodeType

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


@dataclass(frozen=True, slots=True)
class StepBuildContext:
    """Inputs for a single step-builder invocation.

    Attributes:
        step: The partially-assembled YAML step dict; builders mutate
            this in place to add type-specific fields.
        config: Read-only node config copied from the WorkflowNode.
        outgoing_edges: Read-only list of ``(target_node_id, edge_type)``
            tuples for branch enumeration.
    """

    step: dict[str, object]
    config: Mapping[str, object]
    outgoing_edges: Sequence[tuple[str, WorkflowEdgeType]]

    def __post_init__(self) -> None:
        """Freeze the read-only inputs so handlers cannot mutate them.

        ``step`` stays mutable: it is the intended output slot the builders
        write to. ``config`` is deep-copied before being wrapped in
        ``MappingProxyType`` so that nested dicts/lists inherited from the
        caller are isolated -- the project's standard "deepcopy at system
        boundaries" rule. ``outgoing_edges`` is converted to ``tuple``; the
        inner ``(target, edge_type)`` pairs are already immutable.
        """
        object.__setattr__(
            self,
            "config",
            MappingProxyType(copy.deepcopy(dict(self.config))),
        )
        object.__setattr__(self, "outgoing_edges", tuple(self.outgoing_edges))


type StepBuilder = Callable[[StepBuildContext], None]


def _assignment_fields(config: Mapping[str, object]) -> dict[str, object]:
    """Return remapped assignment fields, preserving original value types.

    Skips keys whose value is ``None`` or a blank/whitespace-only string so
    callers can rely on the result being safe to splat into the YAML output
    without surfacing ``"None"`` placeholders or empty fields.
    """
    out: dict[str, object] = {}
    for key in _ASSIGNMENT_KEYS:
        if key not in config:
            continue
        value = config[key]
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        out[_ASSIGNMENT_STEP_MAP[key]] = value
    return out


def _build_task(ctx: StepBuildContext) -> None:
    """Copy task config fields into the step, plus an optional embedded assignment."""
    for key in _TASK_CONFIG_KEYS:
        if key in ctx.config:
            ctx.step[key] = ctx.config[key]
    assignment = _assignment_fields(ctx.config)
    if assignment:
        ctx.step["agent_assignment"] = assignment


def _build_assignment(ctx: StepBuildContext) -> None:
    """Copy agent assignment fields into the step, remapped via the step map."""
    ctx.step.update(_assignment_fields(ctx.config))


def _build_conditional(ctx: StepBuildContext) -> None:
    """Add the conditional expression as a top-level ``condition`` field."""
    if "condition_expression" in ctx.config:
        ctx.step["condition"] = ctx.config["condition_expression"]


def _build_parallel_split(ctx: StepBuildContext) -> None:
    """Emit branch targets and optional ``max_concurrency`` for a split node."""
    ctx.step["branches"] = [
        target
        for target, edge_type in ctx.outgoing_edges
        if edge_type == WorkflowEdgeType.PARALLEL_BRANCH
    ]
    if "max_concurrency" in ctx.config:
        ctx.step["max_concurrency"] = ctx.config["max_concurrency"]


def _build_parallel_join(ctx: StepBuildContext) -> None:
    """Emit ``join_strategy`` defaulting to ``"all"``."""
    ctx.step["join_strategy"] = ctx.config.get("join_strategy", "all")


def _build_subworkflow(ctx: StepBuildContext) -> None:
    """Copy the subworkflow reference and optional binding maps."""
    if "subworkflow_id" in ctx.config:
        ctx.step["subworkflow_id"] = ctx.config["subworkflow_id"]
    if "version" in ctx.config:
        ctx.step["version"] = ctx.config["version"]
    if ctx.config.get("input_bindings"):
        ctx.step["input_bindings"] = dict(
            cast("Mapping[str, object]", ctx.config["input_bindings"]),
        )
    if ctx.config.get("output_bindings"):
        ctx.step["output_bindings"] = dict(
            cast("Mapping[str, object]", ctx.config["output_bindings"]),
        )


def _build_verification(ctx: StepBuildContext) -> None:
    """No-op: verification nodes carry no extra fields in the YAML output."""
    del ctx


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
