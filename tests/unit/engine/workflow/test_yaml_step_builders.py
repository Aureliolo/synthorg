"""Unit tests for the WorkflowNodeType step-builder registry."""

from types import MappingProxyType
from typing import Any

import pytest

from synthorg.core.enums import WorkflowEdgeType, WorkflowNodeType
from synthorg.engine.workflow.yaml_step_builders import (
    STEP_BUILDERS,
    StepBuildContext,
)


def _new_step(node_id: str, node_type: WorkflowNodeType) -> dict[str, Any]:
    return {"id": node_id, "type": node_type.value}


def _ctx(
    step: dict[str, Any],
    config: dict[str, Any] | None = None,
    outgoing: list[tuple[str, WorkflowEdgeType]] | None = None,
) -> StepBuildContext:
    return StepBuildContext(
        step=step,
        config=config if config is not None else {},
        outgoing_edges=outgoing if outgoing is not None else [],
    )


# ── Context immutability ─────────────────────────────────────────


@pytest.mark.unit
class TestStepBuildContextImmutability:
    """Read-only inputs are frozen at construction time."""

    def test_config_is_wrapped_in_mappingproxy(self) -> None:
        original: dict[str, Any] = {"k": "v"}
        ctx = _ctx(_new_step("n", WorkflowNodeType.TASK), original)
        assert isinstance(ctx.config, MappingProxyType)

    def test_config_mutation_via_context_is_blocked(self) -> None:
        ctx = _ctx(_new_step("n", WorkflowNodeType.TASK), {"k": "v"})
        with pytest.raises(TypeError):
            ctx.config["k"] = "mutated"  # type: ignore[index]

    def test_config_caller_mutation_after_construction_does_not_leak(self) -> None:
        original: dict[str, Any] = {"k": "v"}
        ctx = _ctx(_new_step("n", WorkflowNodeType.TASK), original)
        original["k"] = "mutated"
        original["new"] = "value"
        assert ctx.config["k"] == "v"
        assert "new" not in ctx.config

    def test_outgoing_edges_is_tuple(self) -> None:
        ctx = _ctx(
            _new_step("n", WorkflowNodeType.PARALLEL_SPLIT),
            outgoing=[("a", WorkflowEdgeType.PARALLEL_BRANCH)],
        )
        assert isinstance(ctx.outgoing_edges, tuple)


# ── Registry exhaustiveness ──────────────────────────────────────


@pytest.mark.unit
class TestRegistryShape:
    """The registry covers every dispatched node type."""

    def test_registry_covers_every_dispatched_node_type(self) -> None:
        # START / END are filtered upstream in `_generate_steps` and never
        # reach the registry, so they're intentionally absent.
        assert set(STEP_BUILDERS.keys()) == set(WorkflowNodeType) - {
            WorkflowNodeType.START,
            WorkflowNodeType.END,
        }

    def test_registry_raises_keyerror_for_unknown_type(self) -> None:
        with pytest.raises(KeyError):
            _ = STEP_BUILDERS[object()]  # type: ignore[index]


# ── TASK ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestTaskBuilder:
    def test_copies_task_config_keys(self) -> None:
        step = _new_step("t1", WorkflowNodeType.TASK)
        config = {
            "title": "Design",
            "task_type": "design",
            "priority": "high",
            "complexity": "medium",
            "coordination_topology": "centralized",
        }
        STEP_BUILDERS[WorkflowNodeType.TASK](_ctx(step, config))
        assert step["title"] == "Design"
        assert step["task_type"] == "design"
        assert step["priority"] == "high"
        assert step["complexity"] == "medium"
        assert step["coordination_topology"] == "centralized"

    def test_skips_missing_task_keys(self) -> None:
        step = _new_step("t1", WorkflowNodeType.TASK)
        STEP_BUILDERS[WorkflowNodeType.TASK](_ctx(step, {"title": "Only title"}))
        assert step == {"id": "t1", "type": "task", "title": "Only title"}

    def test_embedded_assignment_when_routing_present(self) -> None:
        step = _new_step("t1", WorkflowNodeType.TASK)
        config = {
            "title": "Review",
            "routing_strategy": "cost_optimized",
            "role_filter": "senior_engineer",
        }
        STEP_BUILDERS[WorkflowNodeType.TASK](_ctx(step, config))
        assert step["agent_assignment"] == {
            "strategy": "cost_optimized",
            "role": "senior_engineer",
        }

    def test_no_embedded_assignment_when_routing_absent(self) -> None:
        step = _new_step("t1", WorkflowNodeType.TASK)
        STEP_BUILDERS[WorkflowNodeType.TASK](_ctx(step, {"title": "Plain"}))
        assert "agent_assignment" not in step

    def test_skips_null_and_empty_assignment_fields(self) -> None:
        """None / blank assignment values must not surface as ``"None"``/empty."""
        step = _new_step("t1", WorkflowNodeType.TASK)
        config = {
            "title": "Review",
            "routing_strategy": "cost_optimized",
            "role_filter": None,
            "agent_name": "   ",
        }
        STEP_BUILDERS[WorkflowNodeType.TASK](_ctx(step, config))
        assert step["agent_assignment"] == {"strategy": "cost_optimized"}

    def test_no_embedded_assignment_when_all_fields_blank(self) -> None:
        step = _new_step("t1", WorkflowNodeType.TASK)
        config = {
            "title": "Plain",
            "routing_strategy": None,
            "role_filter": "",
            "agent_name": None,
        }
        STEP_BUILDERS[WorkflowNodeType.TASK](_ctx(step, config))
        assert "agent_assignment" not in step

    def test_preserves_non_string_assignment_value_types(self) -> None:
        """Non-string assignment values pass through verbatim (no ``str()``)."""
        step = _new_step("t1", WorkflowNodeType.TASK)
        config = {
            "title": "Numeric",
            "routing_strategy": "round_robin",
            "agent_name": 42,
        }
        STEP_BUILDERS[WorkflowNodeType.TASK](_ctx(step, config))
        assert step["agent_assignment"] == {
            "strategy": "round_robin",
            "agent_name": 42,
        }


# ── AGENT_ASSIGNMENT ─────────────────────────────────────────────


@pytest.mark.unit
class TestAssignmentBuilder:
    def test_remaps_keys_via_step_map(self) -> None:
        step = _new_step("a1", WorkflowNodeType.AGENT_ASSIGNMENT)
        config = {
            "routing_strategy": "role_based",
            "role_filter": "engineer",
            "agent_name": "agent-007",
        }
        STEP_BUILDERS[WorkflowNodeType.AGENT_ASSIGNMENT](_ctx(step, config))
        assert step["strategy"] == "role_based"
        assert step["role"] == "engineer"
        assert step["agent_name"] == "agent-007"

    def test_partial_assignment_only_emits_present_keys(self) -> None:
        step = _new_step("a1", WorkflowNodeType.AGENT_ASSIGNMENT)
        STEP_BUILDERS[WorkflowNodeType.AGENT_ASSIGNMENT](
            _ctx(step, {"routing_strategy": "role_based"}),
        )
        assert step["strategy"] == "role_based"
        assert "role" not in step
        assert "agent_name" not in step

    def test_skips_null_and_empty_fields(self) -> None:
        step = _new_step("a1", WorkflowNodeType.AGENT_ASSIGNMENT)
        config = {
            "routing_strategy": "role_based",
            "role_filter": None,
            "agent_name": "   ",
        }
        STEP_BUILDERS[WorkflowNodeType.AGENT_ASSIGNMENT](_ctx(step, config))
        assert step["strategy"] == "role_based"
        assert "role" not in step
        assert "agent_name" not in step


# ── CONDITIONAL ──────────────────────────────────────────────────


@pytest.mark.unit
class TestConditionalBuilder:
    def test_adds_condition_when_expression_present(self) -> None:
        step = _new_step("c1", WorkflowNodeType.CONDITIONAL)
        STEP_BUILDERS[WorkflowNodeType.CONDITIONAL](
            _ctx(step, {"condition_expression": "x > 0"}),
        )
        assert step["condition"] == "x > 0"

    def test_no_condition_field_when_expression_absent(self) -> None:
        step = _new_step("c1", WorkflowNodeType.CONDITIONAL)
        STEP_BUILDERS[WorkflowNodeType.CONDITIONAL](_ctx(step))
        assert "condition" not in step


# ── PARALLEL_SPLIT ───────────────────────────────────────────────


@pytest.mark.unit
class TestParallelSplitBuilder:
    def test_collects_only_parallel_branch_targets(self) -> None:
        step = _new_step("s1", WorkflowNodeType.PARALLEL_SPLIT)
        outgoing = [
            ("a", WorkflowEdgeType.PARALLEL_BRANCH),
            ("b", WorkflowEdgeType.PARALLEL_BRANCH),
            ("c", WorkflowEdgeType.SEQUENTIAL),
        ]
        STEP_BUILDERS[WorkflowNodeType.PARALLEL_SPLIT](_ctx(step, outgoing=outgoing))
        assert step["branches"] == ["a", "b"]

    def test_max_concurrency_copied_when_present(self) -> None:
        step = _new_step("s1", WorkflowNodeType.PARALLEL_SPLIT)
        STEP_BUILDERS[WorkflowNodeType.PARALLEL_SPLIT](
            _ctx(
                step,
                {"max_concurrency": 4},
                [("a", WorkflowEdgeType.PARALLEL_BRANCH)],
            ),
        )
        assert step["max_concurrency"] == 4

    def test_max_concurrency_omitted_when_absent(self) -> None:
        step = _new_step("s1", WorkflowNodeType.PARALLEL_SPLIT)
        STEP_BUILDERS[WorkflowNodeType.PARALLEL_SPLIT](_ctx(step))
        assert "max_concurrency" not in step


# ── PARALLEL_JOIN ────────────────────────────────────────────────


@pytest.mark.unit
class TestParallelJoinBuilder:
    def test_join_strategy_default_all(self) -> None:
        step = _new_step("j1", WorkflowNodeType.PARALLEL_JOIN)
        STEP_BUILDERS[WorkflowNodeType.PARALLEL_JOIN](_ctx(step))
        assert step["join_strategy"] == "all"

    def test_join_strategy_from_config(self) -> None:
        step = _new_step("j1", WorkflowNodeType.PARALLEL_JOIN)
        STEP_BUILDERS[WorkflowNodeType.PARALLEL_JOIN](
            _ctx(step, {"join_strategy": "any"}),
        )
        assert step["join_strategy"] == "any"


# ── SUBWORKFLOW ──────────────────────────────────────────────────


@pytest.mark.unit
class TestSubworkflowBuilder:
    def test_copies_reference_fields(self) -> None:
        step = _new_step("sw1", WorkflowNodeType.SUBWORKFLOW)
        config = {
            "subworkflow_id": "wf-42",
            "version": "v3",
            "input_bindings": {"a": "$x"},
            "output_bindings": {"y": "$b"},
        }
        STEP_BUILDERS[WorkflowNodeType.SUBWORKFLOW](_ctx(step, config))
        assert step["subworkflow_id"] == "wf-42"
        assert step["version"] == "v3"
        assert step["input_bindings"] == {"a": "$x"}
        assert step["output_bindings"] == {"y": "$b"}

    def test_omits_empty_bindings(self) -> None:
        step = _new_step("sw1", WorkflowNodeType.SUBWORKFLOW)
        STEP_BUILDERS[WorkflowNodeType.SUBWORKFLOW](
            _ctx(step, {"subworkflow_id": "wf-42"}),
        )
        assert step["subworkflow_id"] == "wf-42"
        assert "input_bindings" not in step
        assert "output_bindings" not in step

    def test_omits_falsy_bindings(self) -> None:
        step = _new_step("sw1", WorkflowNodeType.SUBWORKFLOW)
        STEP_BUILDERS[WorkflowNodeType.SUBWORKFLOW](
            _ctx(
                step,
                {
                    "subworkflow_id": "wf-42",
                    "input_bindings": {},
                    "output_bindings": None,
                },
            ),
        )
        assert "input_bindings" not in step
        assert "output_bindings" not in step


# ── VERIFICATION (no-op) ─────────────────────────────────────────


@pytest.mark.unit
class TestVerificationBuilder:
    def test_verification_is_no_op(self) -> None:
        step = _new_step("v1", WorkflowNodeType.VERIFICATION)
        STEP_BUILDERS[WorkflowNodeType.VERIFICATION](
            _ctx(
                step,
                {"rubric_name": "ignored"},
                [("x", WorkflowEdgeType.VERIFICATION_PASS)],
            ),
        )
        assert step == {"id": "v1", "type": "verification"}
