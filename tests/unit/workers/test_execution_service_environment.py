"""Unit tests for environment provisioning in AgentEngineExecutionService."""

from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from synthorg.core.enums import EnvironmentType
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.errors import EnvironmentProvisionError
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.workspace.environment.protocol import ProvisionedEnvironment
from synthorg.engine.workspace.environment.service import EnvironmentService
from synthorg.hr.registry import AgentRegistryService
from synthorg.tools.sandbox.active_environment import ActiveSandboxEnvironment
from synthorg.tools.sandbox.protocol import SandboxBackend
from synthorg.workers.execution_service import AgentEngineExecutionService
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _provisioned(
    *, image_ref: str | None = None, env_vars: dict[str, str] | None = None
) -> ProvisionedEnvironment:
    # Only the devcontainer image path carries an image_ref; the manifest
    # path carries env additions instead.
    kind = EnvironmentType.DEVCONTAINER if image_ref else EnvironmentType.MANIFEST
    return ProvisionedEnvironment(
        environment_type=kind,
        declaration_hash=NotBlankStr("a" * 64),
        image_ref=NotBlankStr(image_ref) if image_ref else None,
        env_vars=env_vars or {},
    )


def _service(
    *,
    environment_service: EnvironmentService | None = None,
    environment_runner_backend: SandboxBackend | None = None,
) -> AgentEngineExecutionService:
    return AgentEngineExecutionService(
        engine=mock_of[AgentEngine](run=AsyncMock()),
        task_engine=mock_of[TaskEngine](),
        agent_registry=AgentRegistryService(),
        environment_service=environment_service,
        environment_runner_backend=environment_runner_backend,
    )


def _backend(kind: str = "docker") -> SandboxBackend:
    return cast(
        "SandboxBackend",
        mock_of[SandboxBackend](get_backend_type=Mock(return_value=NotBlankStr(kind))),
    )


class TestProvisionEnvironment:
    async def test_returns_none_without_service(self, tmp_path: Path) -> None:
        service = _service()
        result = await service._provision_environment(
            task_id="t1", project_id=NotBlankStr("p1"), workspace_path=tmp_path
        )
        assert result is None

    async def test_returns_none_without_project(self, tmp_path: Path) -> None:
        env_svc = mock_of[EnvironmentService](get_or_provision=AsyncMock())
        service = _service(
            environment_service=env_svc, environment_runner_backend=_backend()
        )
        result = await service._provision_environment(
            task_id="t1", project_id=None, workspace_path=tmp_path
        )
        assert result is None

    async def test_returns_none_without_workspace(self) -> None:
        env_svc = mock_of[EnvironmentService](get_or_provision=AsyncMock())
        service = _service(
            environment_service=env_svc, environment_runner_backend=_backend()
        )
        result = await service._provision_environment(
            task_id="t1", project_id=NotBlankStr("p1"), workspace_path=None
        )
        assert result is None

    async def test_builds_active_env_from_provisioned(self, tmp_path: Path) -> None:
        env_svc = mock_of[EnvironmentService](
            get_or_provision=AsyncMock(
                return_value=_provisioned(
                    image_ref="synthorg-project-p1:abc",
                    env_vars={"VIRTUAL_ENV": "/w/.venv"},
                )
            )
        )
        backend = _backend("docker")
        service = _service(
            environment_service=env_svc, environment_runner_backend=backend
        )

        result = await service._provision_environment(
            task_id="t1", project_id=NotBlankStr("p1"), workspace_path=tmp_path
        )

        assert isinstance(result, ActiveSandboxEnvironment)
        assert result.image_override == "synthorg-project-p1:abc"
        assert result.env_additions == {"VIRTUAL_ENV": "/w/.venv"}
        # Provisioned through the resolved backend's kind.
        kwargs = env_svc.get_or_provision.await_args.kwargs
        assert kwargs["sandbox_kind"] == "docker"

    async def test_fail_loud_on_provision_error(self, tmp_path: Path) -> None:
        env_svc = mock_of[EnvironmentService](
            get_or_provision=AsyncMock(side_effect=EnvironmentProvisionError("boom"))
        )
        service = _service(
            environment_service=env_svc, environment_runner_backend=_backend()
        )
        with pytest.raises(EnvironmentProvisionError):
            await service._provision_environment(
                task_id="t1", project_id=NotBlankStr("p1"), workspace_path=tmp_path
            )
