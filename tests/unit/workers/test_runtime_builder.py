"""Unit tests for the provider-present runtime-services switch."""

from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.api.state import AppState
from synthorg.config.provider_schema import ProviderConfig
from synthorg.config.schema import RootConfig
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry import AgentRegistryService
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.bridge_configs import EngineBridgeConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.workers.execution_service import (
    AgentEngineExecutionService,
    NoProviderExecutionService,
)
from synthorg.workers.runtime_builder import (
    RuntimeServices,
    build_runtime_services,
)
from tests._shared import FakeClock, mock_of

pytestmark = pytest.mark.unit


def _provider_app_state(registry: ProviderRegistry, workspace: Path) -> AppState:
    """Build a mocked AppState for the provider-present path."""
    # ``mock_of[T](...)`` is ``Any`` by design; cast back to the spec so
    # the helper keeps a precise signature for its callers.
    return cast(
        "AppState",
        mock_of[AppState](
            has_active_provider=True,
            provider_registry=registry,
            config=RootConfig(company_name="test-corp"),
            config_resolver=mock_of[ConfigResolver](
                get_float=AsyncMock(return_value=30.0),
                get_str=AsyncMock(return_value="example-medium-001"),
                get_engine_bridge_config=AsyncMock(
                    return_value=EngineBridgeConfig(),
                ),
            ),
            task_engine=mock_of[TaskEngine](),
            agent_registry=AgentRegistryService(),
            approval_store=None,
            clock=FakeClock(),
            event_stream_hub=None,
            interrupt_store=None,
            agent_workspace_root=workspace,
            has_cost_tracker=False,
            has_audit_log=False,
            has_memory_backend=False,
            has_performance_tracker=False,
        ),
    )


class TestProviderPresentSwitch:
    async def test_no_provider_returns_no_provider_runtime(
        self,
        tmp_path: Path,
    ) -> None:
        app_state = mock_of[AppState](has_active_provider=False)
        result = await build_runtime_services(
            app_state,
            workspace_root=tmp_path,
        )
        assert isinstance(result, RuntimeServices)
        assert isinstance(
            result.worker_execution_service,
            NoProviderExecutionService,
        )
        assert result.coordinator is None

    async def test_empty_registry_returns_no_provider_runtime(
        self,
        tmp_path: Path,
    ) -> None:
        app_state = mock_of[AppState](
            has_active_provider=True,
            provider_registry=ProviderRegistry({}),
        )
        result = await build_runtime_services(
            app_state,
            workspace_root=tmp_path,
        )
        assert isinstance(
            result.worker_execution_service,
            NoProviderExecutionService,
        )
        assert result.coordinator is None

    async def test_provider_present_returns_runtime_pair(
        self,
        tmp_path: Path,
    ) -> None:
        registry = ProviderRegistry.from_config(
            {"test-provider": ProviderConfig(driver="scripted")}
        )
        app_state = _provider_app_state(registry, tmp_path)

        result = await build_runtime_services(
            app_state,
            workspace_root=tmp_path,
        )

        assert isinstance(
            result.worker_execution_service,
            AgentEngineExecutionService,
        )
        assert isinstance(result.coordinator, MultiAgentCoordinator)

    async def test_worker_and_coordinator_share_one_engine(
        self,
        tmp_path: Path,
    ) -> None:
        registry = ProviderRegistry.from_config(
            {"test-provider": ProviderConfig(driver="scripted")}
        )
        app_state = _provider_app_state(registry, tmp_path)

        result = await build_runtime_services(
            app_state,
            workspace_root=tmp_path,
        )

        worker = result.worker_execution_service
        coordinator = result.coordinator
        assert isinstance(worker, AgentEngineExecutionService)
        assert coordinator is not None
        # The coordinator's parallel executor must run sub-agents on the
        # exact same boot AgentEngine as the worker execute seam.
        assert coordinator._parallel_executor._engine is worker._engine

    async def test_multiple_providers_warns_and_selects_first(
        self,
        tmp_path: Path,
    ) -> None:
        registry = ProviderRegistry.from_config(
            {
                "test-provider": ProviderConfig(driver="scripted"),
                "test-provider-2": ProviderConfig(driver="scripted"),
            }
        )
        app_state = _provider_app_state(registry, tmp_path)
        # Exercises the >1-provider branch (warning + first-provider
        # selection); it must still build a live runtime, not reject.
        result = await build_runtime_services(
            app_state,
            workspace_root=tmp_path,
        )
        assert isinstance(
            result.worker_execution_service,
            AgentEngineExecutionService,
        )
        assert isinstance(result.coordinator, MultiAgentCoordinator)

    async def test_builds_nonexistent_deep_workspace_path(
        self,
        tmp_path: Path,
    ) -> None:
        registry = ProviderRegistry.from_config(
            {"test-provider": ProviderConfig(driver="scripted")}
        )
        deep = tmp_path / "missing" / "agent" / "workspace"
        app_state = _provider_app_state(registry, deep)
        assert not deep.exists()

        result = await build_runtime_services(
            app_state,
            workspace_root=deep,
        )

        assert isinstance(
            result.worker_execution_service,
            AgentEngineExecutionService,
        )
        assert isinstance(result.coordinator, MultiAgentCoordinator)
        assert deep.is_dir()
