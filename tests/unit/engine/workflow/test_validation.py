"""Tests for workflow definition validation."""

import pytest

from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from synthorg.engine.workflow.enums import WorkflowEdgeType, WorkflowNodeType
from synthorg.engine.workflow.validation import (
    ValidationErrorCode,
    validate_workflow,
)
from tests.unit.engine.workflow.conftest import (
    make_edge as _edge,
)
from tests.unit.engine.workflow.conftest import (
    make_node as _node,
)
from tests.unit.engine.workflow.conftest import (
    make_workflow as _wf,
)

# ── Valid workflows ─────────────────────────────────────────────


class TestValidWorkflows:
    """Workflows that should pass validation."""

    @pytest.mark.unit
    def test_minimal_sequential(self) -> None:
        """start -> task -> end."""
        result = validate_workflow(
            _wf(
                nodes=(
                    _node("s", WorkflowNodeType.START),
                    _node("t", WorkflowNodeType.TASK, title="Do it"),
                    _node("e", WorkflowNodeType.END),
                ),
                edges=(
                    _edge("e1", "s", "t"),
                    _edge("e2", "t", "e"),
                ),
            ),
        )
        assert result.valid
        assert len(result.errors) == 0

    @pytest.mark.unit
    def test_parallel_workflow(self) -> None:
        """start -> split -> [a, b] -> join -> end."""
        result = validate_workflow(
            _wf(
                nodes=(
                    _node("s", WorkflowNodeType.START),
                    _node("split", WorkflowNodeType.PARALLEL_SPLIT),
                    _node("a", WorkflowNodeType.TASK, title="A"),
                    _node("b", WorkflowNodeType.TASK, title="B"),
                    _node("join", WorkflowNodeType.PARALLEL_JOIN),
                    _node("e", WorkflowNodeType.END),
                ),
                edges=(
                    _edge("e1", "s", "split"),
                    _edge("e2", "split", "a", WorkflowEdgeType.PARALLEL_BRANCH),
                    _edge("e3", "split", "b", WorkflowEdgeType.PARALLEL_BRANCH),
                    _edge("e4", "a", "join"),
                    _edge("e5", "b", "join"),
                    _edge("e6", "join", "e"),
                ),
            ),
        )
        assert result.valid

    @pytest.mark.unit
    def test_conditional_workflow(self) -> None:
        """start -> cond -> [true: a, false: b] -> end."""
        result = validate_workflow(
            _wf(
                nodes=(
                    _node("s", WorkflowNodeType.START),
                    _node(
                        "c", WorkflowNodeType.CONDITIONAL, condition_expression="x > 0"
                    ),
                    _node("a", WorkflowNodeType.TASK, title="Yes"),
                    _node("b", WorkflowNodeType.TASK, title="No"),
                    _node("e", WorkflowNodeType.END),
                ),
                edges=(
                    _edge("e1", "s", "c"),
                    _edge("e2", "c", "a", WorkflowEdgeType.CONDITIONAL_TRUE),
                    _edge("e3", "c", "b", WorkflowEdgeType.CONDITIONAL_FALSE),
                    _edge("e4", "a", "e"),
                    _edge("e5", "b", "e"),
                ),
            ),
        )
        assert result.valid


# ── Unreachable nodes ───────────────────────────────────────────


class TestUnreachableNodes:
    """Nodes not reachable from START."""

    @pytest.mark.unit
    def test_disconnected_node(self) -> None:
        result = validate_workflow(
            _wf(
                nodes=(
                    _node("s", WorkflowNodeType.START),
                    _node("t", WorkflowNodeType.TASK, title="Work"),
                    _node("orphan", WorkflowNodeType.TASK, title="Orphan"),
                    _node("e", WorkflowNodeType.END),
                ),
                edges=(
                    _edge("e1", "s", "t"),
                    _edge("e2", "t", "e"),
                ),
            ),
        )
        assert not result.valid
        codes = {err.code for err in result.errors}
        assert ValidationErrorCode.UNREACHABLE_NODE in codes
        orphan_err = next(
            err
            for err in result.errors
            if err.code == ValidationErrorCode.UNREACHABLE_NODE
        )
        assert orphan_err.node_id == "orphan"


# ── END not reachable ───────────────────────────────────────────


class TestEndNotReachable:
    """END node not reachable from START."""

    @pytest.mark.unit
    def test_dead_end_path(self) -> None:
        """start -> task, end is disconnected."""
        result = validate_workflow(
            _wf(
                nodes=(
                    _node("s", WorkflowNodeType.START),
                    _node("t", WorkflowNodeType.TASK, title="Work"),
                    _node("e", WorkflowNodeType.END),
                ),
                edges=(
                    _edge("e1", "s", "t"),
                    # No edge to end
                ),
            ),
        )
        assert not result.valid
        codes = {err.code for err in result.errors}
        assert ValidationErrorCode.END_NOT_REACHABLE in codes


# ── Conditional edge constraints ────────────────────────────────


class TestConditionalConstraints:
    """Conditional node edge type requirements."""

    @pytest.mark.unit
    def test_missing_true_branch(self) -> None:
        result = validate_workflow(
            _wf(
                nodes=(
                    _node("s", WorkflowNodeType.START),
                    _node("c", WorkflowNodeType.CONDITIONAL),
                    _node("b", WorkflowNodeType.TASK, title="False"),
                    _node("e", WorkflowNodeType.END),
                ),
                edges=(
                    _edge("e1", "s", "c"),
                    _edge("e2", "c", "b", WorkflowEdgeType.CONDITIONAL_FALSE),
                    _edge("e3", "b", "e"),
                ),
            ),
        )
        assert not result.valid
        codes = {err.code for err in result.errors}
        assert ValidationErrorCode.CONDITIONAL_MISSING_TRUE in codes

    @pytest.mark.unit
    def test_missing_false_branch(self) -> None:
        result = validate_workflow(
            _wf(
                nodes=(
                    _node("s", WorkflowNodeType.START),
                    _node("c", WorkflowNodeType.CONDITIONAL),
                    _node("a", WorkflowNodeType.TASK, title="True"),
                    _node("e", WorkflowNodeType.END),
                ),
                edges=(
                    _edge("e1", "s", "c"),
                    _edge("e2", "c", "a", WorkflowEdgeType.CONDITIONAL_TRUE),
                    _edge("e3", "a", "e"),
                ),
            ),
        )
        assert not result.valid
        codes = {err.code for err in result.errors}
        assert ValidationErrorCode.CONDITIONAL_MISSING_FALSE in codes

    @pytest.mark.unit
    def test_extra_non_conditional_outgoing(self) -> None:
        result = validate_workflow(
            _wf(
                nodes=(
                    _node("s", WorkflowNodeType.START),
                    _node("c", WorkflowNodeType.CONDITIONAL),
                    _node("a", WorkflowNodeType.TASK, title="True"),
                    _node("b", WorkflowNodeType.TASK, title="False"),
                    _node("x", WorkflowNodeType.TASK, title="Extra"),
                    _node("e", WorkflowNodeType.END),
                ),
                edges=(
                    _edge("e1", "s", "c"),
                    _edge("e2", "c", "a", WorkflowEdgeType.CONDITIONAL_TRUE),
                    _edge("e3", "c", "b", WorkflowEdgeType.CONDITIONAL_FALSE),
                    _edge("e4", "c", "x", WorkflowEdgeType.SEQUENTIAL),
                    _edge("e5", "a", "e"),
                    _edge("e6", "b", "e"),
                    _edge("e7", "x", "e"),
                ),
            ),
        )
        assert not result.valid
        codes = {err.code for err in result.errors}
        assert ValidationErrorCode.CONDITIONAL_EXTRA_OUTGOING in codes


# ── Parallel split constraints ──────────────────────────────────


class TestParallelSplitConstraints:
    """Parallel split node branch requirements."""

    @pytest.mark.unit
    def test_single_branch_rejected(self) -> None:
        result = validate_workflow(
            _wf(
                nodes=(
                    _node("s", WorkflowNodeType.START),
                    _node("split", WorkflowNodeType.PARALLEL_SPLIT),
                    _node("a", WorkflowNodeType.TASK, title="A"),
                    _node("join", WorkflowNodeType.PARALLEL_JOIN),
                    _node("e", WorkflowNodeType.END),
                ),
                edges=(
                    _edge("e1", "s", "split"),
                    _edge("e2", "split", "a", WorkflowEdgeType.PARALLEL_BRANCH),
                    _edge("e3", "a", "join"),
                    _edge("e4", "join", "e"),
                ),
            ),
        )
        assert not result.valid
        codes = {err.code for err in result.errors}
        assert ValidationErrorCode.SPLIT_TOO_FEW_BRANCHES in codes

    @pytest.mark.unit
    def test_zero_branches_rejected(self) -> None:
        result = validate_workflow(
            _wf(
                nodes=(
                    _node("s", WorkflowNodeType.START),
                    _node("split", WorkflowNodeType.PARALLEL_SPLIT),
                    _node("e", WorkflowNodeType.END),
                ),
                edges=(
                    _edge("e1", "s", "split"),
                    _edge("e2", "split", "e"),
                ),
            ),
        )
        assert not result.valid
        codes = {err.code for err in result.errors}
        assert ValidationErrorCode.SPLIT_TOO_FEW_BRANCHES in codes


# ── Task config completeness ───────────────────────────────────


class TestTaskConfig:
    """Task node config requirements."""

    @pytest.mark.unit
    def test_missing_title(self) -> None:
        result = validate_workflow(
            _wf(
                nodes=(
                    _node("s", WorkflowNodeType.START),
                    _node("t", WorkflowNodeType.TASK),  # No title in config
                    _node("e", WorkflowNodeType.END),
                ),
                edges=(
                    _edge("e1", "s", "t"),
                    _edge("e2", "t", "e"),
                ),
            ),
        )
        assert not result.valid
        codes = {err.code for err in result.errors}
        assert ValidationErrorCode.TASK_MISSING_TITLE in codes

    @pytest.mark.unit
    def test_blank_title(self) -> None:
        result = validate_workflow(
            _wf(
                nodes=(
                    _node("s", WorkflowNodeType.START),
                    _node("t", WorkflowNodeType.TASK, title="  "),
                    _node("e", WorkflowNodeType.END),
                ),
                edges=(
                    _edge("e1", "s", "t"),
                    _edge("e2", "t", "e"),
                ),
            ),
        )
        assert not result.valid
        codes = {err.code for err in result.errors}
        assert ValidationErrorCode.TASK_MISSING_TITLE in codes


# ── Cycle detection ─────────────────────────────────────────────


class TestCycleDetection:
    """Graph cycle detection."""

    @pytest.mark.unit
    def test_simple_cycle(self) -> None:
        """start -> a -> b -> a (cycle) and end unreachable."""
        result = validate_workflow(
            _wf(
                nodes=(
                    _node("s", WorkflowNodeType.START),
                    _node("a", WorkflowNodeType.TASK, title="A"),
                    _node("b", WorkflowNodeType.TASK, title="B"),
                    _node("e", WorkflowNodeType.END),
                ),
                edges=(
                    _edge("e1", "s", "a"),
                    _edge("e2", "a", "b"),
                    _edge("e3", "b", "a"),  # cycle
                ),
            ),
        )
        assert not result.valid
        codes = {err.code for err in result.errors}
        assert ValidationErrorCode.CYCLE_DETECTED in codes


# ── Verification node validation ─────────────────────────────────


def _verification_wf(
    verify_edges: tuple[WorkflowEdgeType, ...] = (
        WorkflowEdgeType.VERIFICATION_PASS,
        WorkflowEdgeType.VERIFICATION_FAIL,
        WorkflowEdgeType.VERIFICATION_REFER,
    ),
    verify_config: dict[str, object] | None = None,
) -> WorkflowDefinition:
    """Build a workflow with a verification node and given edges."""
    cfg = (
        verify_config
        if verify_config is not None
        else {
            "rubric_name": "test-rubric",
            "evaluator_agent_id": "eval-agent",
        }
    )
    edge_list: list[WorkflowEdge] = [_edge("e1", "s", "v")]
    target_nodes: list[WorkflowNode] = []
    for i, et in enumerate(verify_edges):
        tid = f"target-{i}"
        edge_list.append(_edge(f"ev{i}", "v", tid, et))
        target_nodes.append(_node(tid, WorkflowNodeType.TASK, title=tid))
    edge_list.extend(_edge(f"e-{tn.id}-end", tn.id, "e") for tn in target_nodes)
    verify_node = _node("v", WorkflowNodeType.VERIFICATION, **cfg)
    return _wf(
        nodes=(
            _node("s", WorkflowNodeType.START),
            verify_node,
            *target_nodes,
            _node("e", WorkflowNodeType.END),
        ),
        edges=tuple(edge_list),
    )


class TestVerificationEdgeValidation:
    """Verification node edge constraints."""

    @pytest.mark.unit
    def test_valid_verification_node(self) -> None:
        result = validate_workflow(_verification_wf())
        assert result.valid

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("verify_edges", "expected_code"),
        [
            (
                (
                    WorkflowEdgeType.VERIFICATION_FAIL,
                    WorkflowEdgeType.VERIFICATION_REFER,
                ),
                ValidationErrorCode.VERIFICATION_MISSING_PASS,
            ),
            (
                (
                    WorkflowEdgeType.VERIFICATION_PASS,
                    WorkflowEdgeType.VERIFICATION_REFER,
                ),
                ValidationErrorCode.VERIFICATION_MISSING_FAIL,
            ),
            (
                (
                    WorkflowEdgeType.VERIFICATION_PASS,
                    WorkflowEdgeType.VERIFICATION_FAIL,
                ),
                ValidationErrorCode.VERIFICATION_MISSING_REFER,
            ),
        ],
    )
    def test_missing_required_verification_edge(
        self,
        verify_edges: tuple[WorkflowEdgeType, ...],
        expected_code: ValidationErrorCode,
    ) -> None:
        result = validate_workflow(_verification_wf(verify_edges=verify_edges))
        assert not result.valid
        assert expected_code in {err.code for err in result.errors}

    @pytest.mark.unit
    def test_duplicate_pass_edge(self) -> None:
        result = validate_workflow(
            _verification_wf(
                verify_edges=(
                    WorkflowEdgeType.VERIFICATION_PASS,
                    WorkflowEdgeType.VERIFICATION_PASS,
                    WorkflowEdgeType.VERIFICATION_FAIL,
                    WorkflowEdgeType.VERIFICATION_REFER,
                ),
            ),
        )
        assert not result.valid
        codes = {err.code for err in result.errors}
        assert ValidationErrorCode.VERIFICATION_DUPLICATE_EDGE in codes

    @pytest.mark.unit
    def test_non_verification_edge_from_verification_node(self) -> None:
        result = validate_workflow(
            _verification_wf(
                verify_edges=(
                    WorkflowEdgeType.VERIFICATION_PASS,
                    WorkflowEdgeType.VERIFICATION_FAIL,
                    WorkflowEdgeType.VERIFICATION_REFER,
                    WorkflowEdgeType.SEQUENTIAL,
                ),
            ),
        )
        assert not result.valid
        codes = {err.code for err in result.errors}
        assert ValidationErrorCode.VERIFICATION_EXTRA_OUTGOING in codes


class TestVerificationEdgeScope:
    """Verification edges must not leave non-verification nodes."""

    @pytest.mark.unit
    def test_verification_edge_from_task_node(self) -> None:
        result = validate_workflow(
            _wf(
                nodes=(
                    _node("s", WorkflowNodeType.START),
                    _node("t", WorkflowNodeType.TASK, title="Work"),
                    _node("e", WorkflowNodeType.END),
                ),
                edges=(
                    _edge("e1", "s", "t"),
                    _edge(
                        "e2",
                        "t",
                        "e",
                        WorkflowEdgeType.VERIFICATION_PASS,
                    ),
                ),
            ),
        )
        assert not result.valid
        codes = {err.code for err in result.errors}
        assert ValidationErrorCode.VERIFICATION_EDGE_OUTSIDE in codes


class TestVerificationConfigValidation:
    """Verification node config constraints."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "verify_config",
        [
            {"evaluator_agent_id": "eval-agent"},
            {"rubric_name": "test-rubric"},
            {"rubric_name": "  ", "evaluator_agent_id": "eval-agent"},
            {"rubric_name": "test-rubric", "evaluator_agent_id": "  "},
            {"rubric_name": 123, "evaluator_agent_id": "eval-agent"},
            {"rubric_name": "test-rubric", "evaluator_agent_id": 456},
        ],
    )
    def test_invalid_verification_config(
        self,
        verify_config: dict[str, object],
    ) -> None:
        result = validate_workflow(
            _verification_wf(verify_config=verify_config),
        )
        assert not result.valid
        codes = {err.code for err in result.errors}
        assert ValidationErrorCode.VERIFICATION_MISSING_CONFIG in codes
