"""The governed forge / chat tools reach the agent's per-run registry.

The forge and chat agent tools are wired per run inside
``AgentEngine._make_tool_invoker`` via ``registry_with_forge_tools`` /
``registry_with_chat_tools``. These tests pin that engine-level wiring:
when the boot-scoped runtime bundle and an approval store are present, an
agent gets the ``forge_*`` / ``chat_*`` tools in its permitted set; when
the runtime is absent the tools are not registered (the feature is off).
Governance behaviour of the tools themselves is covered in the
``tests/unit/tools/forge`` and ``tests/unit/tools/chat`` suites.
"""

from typing import override

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.persistence.integration_inmemory import InMemoryConnectionRepository
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.chat._runtime import ChatToolsRuntime
from synthorg.tools.forge._runtime import ForgeToolsRuntime
from synthorg.tools.registry import ToolRegistry
from tests._shared.scripted_provider import ScriptedProvider, make_e2e_identity

pytestmark = pytest.mark.unit

_TIMEOUT_SECONDS = 30.0
_MAX_READ_CHARS = 100_000
_FORGE_TOOL_NAMES = frozenset(
    {"forge_repo", "forge_issue", "forge_pull_request", "forge_ci"}
)
_CHAT_TOOL_NAMES = frozenset({"chat_messages", "chat_directory"})


class _StubSecretBackend:
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
    return ConnectionCatalog(InMemoryConnectionRepository(), _StubSecretBackend())


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


def _engine(*, forge: bool = False, chat: bool = False) -> AgentEngine:
    registry = ToolRegistry([_StubTool(name="stub", category=ToolCategory.OTHER)])
    return AgentEngine(
        provider=ScriptedProvider([]),
        tool_registry=registry,
        approval_store=ApprovalStore(),
        forge_tools_runtime=_forge_runtime() if forge else None,
        chat_tools_runtime=_chat_runtime() if chat else None,
    )


def _permitted_names(engine: AgentEngine) -> set[str]:
    invoker = engine._make_tool_invoker(make_e2e_identity())
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
