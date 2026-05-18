"""Unit tests for the provider-present worker-execution-service switch."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from synthorg.api.state import AppState
from synthorg.config.provider_schema import ProviderConfig
from synthorg.config.schema import RootConfig
from synthorg.core.clock import SystemClock
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry import AgentRegistryService
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.resolver import ConfigResolver
from synthorg.workers.execution_service import (
    AgentEngineExecutionService,
    NoProviderExecutionService,
)
from synthorg.workers.runtime_builder import build_worker_execution_service
from tests._shared import mock_of

pytestmark = pytest.mark.unit


class TestProviderPresentSwitch:
    async def test_no_provider_returns_no_provider_service(
        self,
        tmp_path: Path,
    ) -> None:
        app_state = mock_of[AppState](has_active_provider=False)
        service = await build_worker_execution_service(
            app_state,
            workspace_root=tmp_path,
        )
        assert isinstance(service, NoProviderExecutionService)

    async def test_empty_registry_returns_no_provider_service(
        self,
        tmp_path: Path,
    ) -> None:
        app_state = mock_of[AppState](
            has_active_provider=True,
            provider_registry=ProviderRegistry({}),
        )
        service = await build_worker_execution_service(
            app_state,
            workspace_root=tmp_path,
        )
        assert isinstance(service, NoProviderExecutionService)

    async def test_provider_present_returns_agent_engine_service(
        self,
        tmp_path: Path,
    ) -> None:
        registry = ProviderRegistry.from_config(
            {"test-provider": ProviderConfig(driver="scripted")}
        )
        app_state = mock_of[AppState](
            has_active_provider=True,
            provider_registry=registry,
            config=RootConfig(company_name="test-corp"),
            config_resolver=mock_of[ConfigResolver](
                get_float=AsyncMock(return_value=30.0),
            ),
            task_engine=mock_of[TaskEngine](),
            agent_registry=AgentRegistryService(),
            approval_store=None,
            clock=SystemClock(),
            event_stream_hub=None,
            interrupt_store=None,
            has_cost_tracker=False,
            has_audit_log=False,
            has_memory_backend=False,
        )
        service = await build_worker_execution_service(
            app_state,
            workspace_root=tmp_path,
        )
        assert isinstance(service, AgentEngineExecutionService)

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
        app_state = mock_of[AppState](
            has_active_provider=True,
            provider_registry=registry,
            config=RootConfig(company_name="test-corp"),
            config_resolver=mock_of[ConfigResolver](
                get_float=AsyncMock(return_value=30.0),
            ),
            task_engine=mock_of[TaskEngine](),
            agent_registry=AgentRegistryService(),
            approval_store=None,
            clock=SystemClock(),
            event_stream_hub=None,
            interrupt_store=None,
            has_cost_tracker=False,
            has_audit_log=False,
            has_memory_backend=False,
        )
        # Exercises the >1-provider branch (warning + first-provider
        # selection); it must still build a live runtime, not reject.
        service = await build_worker_execution_service(
            app_state,
            workspace_root=tmp_path,
        )
        assert isinstance(service, AgentEngineExecutionService)

    async def test_builds_nonexistent_deep_workspace_path(
        self,
        tmp_path: Path,
    ) -> None:
        registry = ProviderRegistry.from_config(
            {"test-provider": ProviderConfig(driver="scripted")}
        )
        app_state = mock_of[AppState](
            has_active_provider=True,
            provider_registry=registry,
            config=RootConfig(company_name="test-corp"),
            config_resolver=mock_of[ConfigResolver](
                get_float=AsyncMock(return_value=30.0),
            ),
            task_engine=mock_of[TaskEngine](),
            agent_registry=AgentRegistryService(),
            approval_store=None,
            clock=SystemClock(),
            event_stream_hub=None,
            interrupt_store=None,
            has_cost_tracker=False,
            has_audit_log=False,
            has_memory_backend=False,
        )
        deep = tmp_path / "missing" / "agent" / "workspace"
        assert not deep.exists()

        service = await build_worker_execution_service(
            app_state,
            workspace_root=deep,
        )

        assert isinstance(service, AgentEngineExecutionService)
        assert deep.is_dir()
