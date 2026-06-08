"""Tests for verification node dispatch and routing."""

import pytest

from synthorg.engine.quality.verification import VerificationVerdict
from synthorg.engine.workflow.enums import (
    WorkflowEdgeType,
    WorkflowNodeExecutionStatus,
    WorkflowNodeType,
)
from synthorg.engine.workflow.execution_activation_helpers import (
    process_verification_node,
)
from tests.unit.engine.workflow.conftest import make_node as _node


def _outgoing(
    nid: str = "v1",
) -> dict[str, list[tuple[str, WorkflowEdgeType]]]:
    return {
        nid: [
            ("pass-target", WorkflowEdgeType.VERIFICATION_PASS),
            ("fail-target", WorkflowEdgeType.VERIFICATION_FAIL),
            ("refer-target", WorkflowEdgeType.VERIFICATION_REFER),
        ],
    }


def _adjacency() -> dict[str, list[str]]:
    return {
        "pass-target": ["pass-task"],
        "fail-target": ["fail-task"],
        "refer-target": ["refer-task"],
    }


@pytest.mark.unit
class TestProcessVerificationNode:
    def test_pass_verdict_completes(self) -> None:
        node = _node("v1", WorkflowNodeType.VERIFICATION)
        skipped: set[str] = set()
        result = process_verification_node(
            "v1",
            node,
            _outgoing(),
            _adjacency(),
            skipped,
            "exec-1",
            VerificationVerdict.PASS,
        )
        assert result.status == WorkflowNodeExecutionStatus.COMPLETED

    def test_pass_verdict_skips_fail_and_refer(self) -> None:
        node = _node("v1", WorkflowNodeType.VERIFICATION)
        skipped: set[str] = set()
        process_verification_node(
            "v1",
            node,
            _outgoing(),
            _adjacency(),
            skipped,
            "exec-1",
            VerificationVerdict.PASS,
        )
        assert "fail-target" in skipped
        assert "fail-task" in skipped
        assert "refer-target" in skipped
        assert "refer-task" in skipped
        assert "pass-target" not in skipped

    def test_fail_verdict_skips_pass_and_refer(self) -> None:
        node = _node("v1", WorkflowNodeType.VERIFICATION)
        skipped: set[str] = set()
        process_verification_node(
            "v1",
            node,
            _outgoing(),
            _adjacency(),
            skipped,
            "exec-1",
            VerificationVerdict.FAIL,
        )
        assert "pass-target" in skipped
        assert "pass-task" in skipped
        assert "refer-target" in skipped
        assert "refer-task" in skipped
        assert "fail-target" not in skipped

    def test_refer_verdict_skips_pass_and_fail(self) -> None:
        node = _node("v1", WorkflowNodeType.VERIFICATION)
        skipped: set[str] = set()
        process_verification_node(
            "v1",
            node,
            _outgoing(),
            _adjacency(),
            skipped,
            "exec-1",
            VerificationVerdict.REFER,
        )
        assert "pass-target" in skipped
        assert "pass-task" in skipped
        assert "fail-target" in skipped
        assert "fail-task" in skipped
        assert "refer-target" not in skipped

    @pytest.mark.parametrize(
        "verdict",
        [
            VerificationVerdict.PASS,
            VerificationVerdict.FAIL,
            VerificationVerdict.REFER,
        ],
    )
    def test_all_verdicts_complete(self, verdict: VerificationVerdict) -> None:
        node = _node("v1", WorkflowNodeType.VERIFICATION)
        skipped: set[str] = set()
        result = process_verification_node(
            "v1",
            node,
            _outgoing(),
            _adjacency(),
            skipped,
            "exec-1",
            verdict,
        )
        assert result.status == WorkflowNodeExecutionStatus.COMPLETED
        assert result.node_type == WorkflowNodeType.VERIFICATION
