"""The governed connection tools reach the agent's per-run registry.

The forge, chat, deploy and publish agent tools are wired per run inside
``AgentEngine._make_tool_invoker`` via the ``registry_with_*_tools``
family. These tests pin that engine-level wiring: when the boot-scoped
runtime bundle and an approval store are present, an agent gets that
family's tools in its permitted set; when the runtime is absent the tools
are not registered (the feature is off). Governance behaviour of the tools
themselves is covered in the per-family ``tests/unit/tools/`` suites.
"""

from pathlib import Path
from typing import override

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.types import NotBlankStr
from synthorg.engine._agent_tool_registry import (
    registry_with_deploy_tools,
    registry_with_publish_tools,
)
from synthorg.engine.agent_engine import AgentEngine
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.persistence.integration_inmemory import InMemoryConnectionRepository
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.chat._runtime import ChatToolsRuntime
from synthorg.tools.connection_tool_runtimes import ConnectionToolRuntimes
from synthorg.tools.deploy._runtime import DeployToolsRuntime
from synthorg.tools.deploy.deploy_tools import DeployReleaseTool
from synthorg.tools.forge._runtime import ForgeToolsRuntime
from synthorg.tools.publish._runtime import PublishToolsRuntime
from synthorg.tools.publish.publish_tools import PublishPushTool
from synthorg.tools.registry import ToolRegistry
from tests._shared.scripted_provider import ScriptedProvider, make_e2e_identity

pytestmark = pytest.mark.unit

_TIMEOUT_SECONDS = 30.0
_MAX_READ_CHARS = 100_000
_MAX_LOG_CHARS = 50_000
_MAX_MANIFEST_BYTES = 1_048_576
_MAX_IMAGE_BYTES = 2_147_483_648
_TARGETS = frozenset({"staging"})
_FORGE_TOOL_NAMES = frozenset(
    {"forge_repo", "forge_issue", "forge_pull_request", "forge_ci"}
)
_CHAT_TOOL_NAMES = frozenset({"chat_messages", "chat_directory"})
_DEPLOY_TOOL_NAMES = frozenset({"deploy_run", "deploy_release"})
_PUBLISH_TOOL_NAMES = frozenset({"publish_inspect", "publish_push"})


class _FakeSecretBackend:
    """Minimal ``SecretBackend`` so the catalog constructs."""

    @property
    def backend_name(self) -> NotBlankStr:
        return NotBlankStr("stub")

    async def store(self, secret_id: NotBlankStr, value: bytes) -> None:
        del secret_id, value

    async def retrieve(self, secret_id: NotBlankStr) -> bytes | None:
        del secret_id
        return None

    async def delete(self, secret_id: NotBlankStr) -> bool:
        del secret_id
        return False

    async def rotate(self, old_id: NotBlankStr, new_value: bytes) -> NotBlankStr:
        del new_value
        return old_id

    async def close(self) -> None:
        return None


class _StubTool(BaseTool):
    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        del arguments
        return ToolExecutionResult(content="stub")


def _catalog() -> ConnectionCatalog:
    return ConnectionCatalog(InMemoryConnectionRepository(), _FakeSecretBackend())


def _forge_runtime() -> ForgeToolsRuntime:
    return ForgeToolsRuntime(
        connection_catalog=_catalog(),
        connection_name="forge-conn",
        timeout_seconds=_TIMEOUT_SECONDS,
        max_read_chars=_MAX_READ_CHARS,
    )


def _chat_runtime() -> ChatToolsRuntime:
    return ChatToolsRuntime(
        connection_catalog=_catalog(),
        connection_name="chat-conn",
        timeout_seconds=_TIMEOUT_SECONDS,
    )


def _deploy_runtime() -> DeployToolsRuntime:
    return DeployToolsRuntime(
        connection_catalog=_catalog(),
        allowed_targets=_TARGETS,
        timeout_seconds=_TIMEOUT_SECONDS,
        max_log_chars=_MAX_LOG_CHARS,
    )


def _publish_runtime(workspace_root: Path) -> PublishToolsRuntime:
    return PublishToolsRuntime(
        connection_catalog=_catalog(),
        allowed_targets=_TARGETS,
        timeout_seconds=_TIMEOUT_SECONDS,
        max_manifest_bytes=_MAX_MANIFEST_BYTES,
        max_image_bytes=_MAX_IMAGE_BYTES,
        workspace_root=workspace_root,
    )


def _engine(
    *,
    forge: bool = False,
    chat: bool = False,
    deploy: bool = False,
    publish_root: Path | None = None,
) -> AgentEngine:
    registry = ToolRegistry([_StubTool(name="stub", category=ToolCategory.OTHER)])
    return AgentEngine(
        provider=ScriptedProvider([]),
        tool_registry=registry,
        approval_store=ApprovalStore(),
        connection_tool_runtimes=ConnectionToolRuntimes(
            forge=_forge_runtime() if forge else None,
            chat=_chat_runtime() if chat else None,
            deploy=_deploy_runtime() if deploy else None,
            publish=(
                _publish_runtime(publish_root) if publish_root is not None else None
            ),
        ),
    )


def _permitted_names(engine: AgentEngine) -> set[str]:
    invoker = engine._make_tool_invoker(make_e2e_identity(), memory_strategy=None)
    assert invoker is not None
    return {d.name for d in invoker.get_permitted_definitions()}


class TestAgentEngineForgeWiring:
    """``forge_*`` tools are registered per run only when the runtime is wired."""

    def test_forge_tools_registered_when_runtime_wired(self) -> None:
        names = _permitted_names(_engine(forge=True))
        assert names >= _FORGE_TOOL_NAMES

    def test_no_forge_tools_when_runtime_absent(self) -> None:
        names = _permitted_names(_engine(forge=False))
        assert names.isdisjoint(_FORGE_TOOL_NAMES)


class TestAgentEngineChatWiring:
    """``chat_*`` tools are registered per run only when the runtime is wired."""

    def test_chat_tools_registered_when_runtime_wired(self) -> None:
        names = _permitted_names(_engine(chat=True))
        assert names >= _CHAT_TOOL_NAMES

    def test_no_chat_tools_when_runtime_absent(self) -> None:
        names = _permitted_names(_engine(chat=False))
        assert names.isdisjoint(_CHAT_TOOL_NAMES)


class TestAgentEngineDeployWiring:
    """``deploy_*`` tools are registered per run only when wired."""

    def test_deploy_tools_registered_when_runtime_wired(self) -> None:
        names = _permitted_names(_engine(deploy=True))
        assert names >= _DEPLOY_TOOL_NAMES

    def test_no_deploy_tools_when_runtime_absent(self) -> None:
        names = _permitted_names(_engine(deploy=False))
        assert names.isdisjoint(_DEPLOY_TOOL_NAMES)


class TestAgentEnginePublishWiring:
    """``publish_*`` tools are registered per run only when wired."""

    def test_publish_tools_registered_when_runtime_wired(self, tmp_path: Path) -> None:
        names = _permitted_names(_engine(publish_root=tmp_path))
        assert names >= _PUBLISH_TOOL_NAMES

    def test_no_publish_tools_when_runtime_absent(self) -> None:
        names = _permitted_names(_engine(publish_root=None))
        assert names.isdisjoint(_PUBLISH_TOOL_NAMES)


class TestDestructiveToolsCarryTheRunIdentity:
    """The audit actor on a destructive tool is the run's own identity.

    ``require_admin_guardrails`` refuses a call it cannot attribute, and it
    runs before the approval gate, so an actor that silently became ``None``
    would refuse every deploy and every publish while every registration
    test above still passed on tool names alone.
    """

    def test_deploy_release_is_bound_to_the_caller(self) -> None:
        identity = make_e2e_identity()
        registry = registry_with_deploy_tools(
            ToolRegistry([]),
            _deploy_runtime(),
            approval_store=ApprovalStore(),
            identity=identity,
        )
        release = next(
            t for t in registry.all_tools() if isinstance(t, DeployReleaseTool)
        )
        assert release._actor is identity

    def test_publish_push_is_bound_to_the_caller(self, tmp_path: Path) -> None:
        identity = make_e2e_identity()
        registry = registry_with_publish_tools(
            ToolRegistry([]),
            _publish_runtime(tmp_path),
            approval_store=ApprovalStore(),
            identity=identity,
        )
        push = next(t for t in registry.all_tools() if isinstance(t, PublishPushTool))
        assert push._actor is identity
