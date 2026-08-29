"""Unit tests for the completion-oracle worker boot helpers.

Covers the config resolver's fail-safe fallback and the ``attach_completion_
oracle_gates`` seam that both the startup wiring and the hot-reload path call:
a rebuilt oracle runtime must (re-)attach its gates to the persistent review
gate so the oracle settings are genuinely hot-reloadable, a disabled runtime
must detach them, and a persistence-less boot (no review gate) is a no-op.
"""

from types import SimpleNamespace

import pytest

from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.task_enums import Stakes
from synthorg.engine.completion_oracle.builder import CompletionOracleRuntime
from synthorg.engine.completion_oracle.evaluator import BuildTestOracle
from synthorg.engine.completion_oracle.gate import CompletionOracleGateService
from synthorg.engine.completion_oracle.protocol import (
    CompletionOracleReportRepository,
)
from synthorg.engine.completion_oracle.runner import ReviewerAgentEngineRunner
from synthorg.engine.completion_oracle.tools.submit_verdict import (
    SubmitCompletionOracleVerdictTool,
)
from synthorg.engine.review_gate import ReviewGateService
from synthorg.engine.workspace.state import WorkspaceStateSlice
from synthorg.workers import _completion_oracle_runtime
from synthorg.workers._completion_oracle_runtime import (
    attach_completion_oracle_gates,
)
from tests._shared.mock_of import mock_of

pytestmark = pytest.mark.unit


class _FakeAppState:
    """Duck-typed ``AppState`` exposing the slices the attach reads.

    The workspace slice is here because the build/test gate is wired with the
    project root: a skeleton's suite fails by design, so the gate reads what
    the project declared pending before calling that a broken build.
    """

    def __init__(self, review_gate: object) -> None:
        self._slices: dict[type, SimpleNamespace] = {
            ApprovalStateSlice: SimpleNamespace(review_gate=review_gate),
            WorkspaceStateSlice: SimpleNamespace(agent_workspace_root=None),
        }

    def slice(self, slice_type: type) -> SimpleNamespace:
        return self._slices[slice_type]


def _runtime(*, shadow_mode: bool, min_stakes: Stakes) -> CompletionOracleRuntime:
    """A real runtime tuple; only ``gate``/``shadow_mode``/``min_stakes`` matter."""
    return CompletionOracleRuntime(
        submit_tool=mock_of[SubmitCompletionOracleVerdictTool](),
        gate=mock_of[CompletionOracleGateService](),
        report_repo=mock_of[CompletionOracleReportRepository](),
        runner=mock_of[ReviewerAgentEngineRunner](),
        shadow_mode=shadow_mode,
        min_stakes=min_stakes,
    )


def test_attach_no_op_when_no_review_gate() -> None:
    # Persistence-less boot: no review gate wired -> the seam returns cleanly
    # without touching a records repo (which would need a backend).
    app_state = _FakeAppState(review_gate=None)
    attach_completion_oracle_gates(
        app_state,  # type: ignore[arg-type]
        enabled=True,
        completion_oracle_runtime=_runtime(shadow_mode=False, min_stakes=Stakes.LOW),
    )


def test_attach_disabled_clears_both_gates() -> None:
    # Explicit disablement (enabled=False, no runtime): both gates cleared.
    gate_service = mock_of[ReviewGateService]()
    app_state = _FakeAppState(review_gate=gate_service)

    attach_completion_oracle_gates(
        app_state,  # type: ignore[arg-type]
        enabled=False,
        completion_oracle_runtime=None,
    )

    gate_service.set_build_test_gate.assert_called_once_with(None, records=None)
    gate_service.set_completion_oracle_gate.assert_called_once_with(
        None, shadow_mode=False, min_stakes=Stakes.LOW
    )


def test_attach_enabled_without_provider_keeps_build_test_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The deterministic build/test gate needs no provider: an enabled oracle
    # with no peer runtime (provider-less / degraded boot) must still attach
    # build/test and only clear the peer-review gate, so a required code task
    # cannot bypass verification.
    records = object()
    monkeypatch.setattr(
        _completion_oracle_runtime,
        "code_execution_records_of",
        lambda _app_state: records,
    )
    gate_service = mock_of[ReviewGateService]()
    app_state = _FakeAppState(review_gate=gate_service)

    attach_completion_oracle_gates(
        app_state,  # type: ignore[arg-type]
        enabled=True,
        completion_oracle_runtime=None,
    )

    args, kwargs = gate_service.set_build_test_gate.call_args
    assert isinstance(args[0], BuildTestOracle)
    assert kwargs["records"] is records
    gate_service.set_completion_oracle_gate.assert_called_once_with(
        None, shadow_mode=False, min_stakes=Stakes.LOW
    )


def test_attach_wires_both_gates_when_runtime_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = object()
    monkeypatch.setattr(
        _completion_oracle_runtime,
        "code_execution_records_of",
        lambda _app_state: records,
    )
    gate_service = mock_of[ReviewGateService]()
    app_state = _FakeAppState(review_gate=gate_service)
    runtime = _runtime(shadow_mode=True, min_stakes=Stakes.HIGH)

    attach_completion_oracle_gates(
        app_state,  # type: ignore[arg-type]
        enabled=True,
        completion_oracle_runtime=runtime,
    )

    # The build/test gate is a fresh BuildTestOracle bound to the live records.
    args, kwargs = gate_service.set_build_test_gate.call_args
    assert isinstance(args[0], BuildTestOracle)
    assert kwargs["records"] is records
    gate_service.set_completion_oracle_gate.assert_called_once_with(
        runtime.gate, shadow_mode=True, min_stakes=Stakes.HIGH
    )
