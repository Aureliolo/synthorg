"""Unit tests for the worker-execution-service swap seam + workspace root."""

from pathlib import Path

import pytest
from structlog.testing import capture_logs

from synthorg.api.state import AppState
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.config.schema import RootConfig
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.workers.execution_service import NoProviderExecutionService
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _app_state() -> AppState:
    return AppState(
        config=RootConfig(company_name="swap-test"),
        approval_store=mock_of[ApprovalStoreProtocol](),
    )


class TestSwapWorkerExecutionService:
    def test_swap_onto_unset_attaches(self) -> None:
        state = _app_state()
        service = NoProviderExecutionService()
        with capture_logs() as logs:
            state.swap_worker_execution_service(service)
        assert state.worker_execution_service is service
        assert any(e.get("transition") == "attached" for e in logs)

    def test_swap_after_set_replaces(self) -> None:
        state = _app_state()
        first = NoProviderExecutionService()
        second = NoProviderExecutionService()
        state.set_worker_execution_service(first)
        with capture_logs() as logs:
            state.swap_worker_execution_service(second)
        assert state.worker_execution_service is second
        assert any(e.get("transition") == "replaced" for e in logs)

    def test_swap_same_instance_is_noop(self) -> None:
        state = _app_state()
        service = NoProviderExecutionService()
        state.swap_worker_execution_service(service)
        with capture_logs() as logs:
            state.swap_worker_execution_service(service)
        assert any(e.get("transition") == "noop" for e in logs)

    def test_swap_replaces_lazy_default(self) -> None:
        state = _app_state()
        # Force the lazy default to materialise first; it needs a
        # task_engine, which an injected app does not configure.
        with pytest.raises(ServiceUnavailableError):
            _ = state.worker_execution_service
        replacement = NoProviderExecutionService()
        state.swap_worker_execution_service(replacement)
        assert state.worker_execution_service is replacement


class TestAgentWorkspaceRoot:
    def test_default_is_absolute_temp_path(self) -> None:
        state = _app_state()
        root = state.agent_workspace_root
        assert root.is_absolute()
        assert "synthorg-agent-workspaces" in str(root)

    def test_set_pins_once(self, tmp_path: Path) -> None:
        state = _app_state()
        state.set_agent_workspace_root(tmp_path)
        assert state.agent_workspace_root == tmp_path
        with pytest.raises(RuntimeError):
            state.set_agent_workspace_root(tmp_path / "other")
