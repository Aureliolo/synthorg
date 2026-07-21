"""The self-editing memory tools must be bound per agent, never shared.

They mutate an agent's own memory, so the identity they carry decides
whose memory they write. Building them into the boot-time registry that
every agent shares would give the whole company one memory bucket: one
agent could read, overwrite and evict another's memories, and the
ownership check would pass because the id really is the one the tool
holds.
"""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.memory.backends.inmemory import InMemoryBackend
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever import ContextInjectionStrategy
from synthorg.memory.self_editing import SelfEditingMemoryStrategy
from synthorg.memory.self_editing_models import SelfEditingMemoryConfig
from synthorg.memory.tools import registry_with_memory_tools
from synthorg.tools.registry import ToolRegistry

pytestmark = pytest.mark.unit

_EXPECTED = {
    "core_memory_read",
    "core_memory_write",
    "archival_memory_search",
    "archival_memory_write",
    "recall_memory_read",
    "recall_memory_write",
}


def _self_editing() -> SelfEditingMemoryStrategy:
    """Build a self-editing strategy over an ephemeral backend."""
    return SelfEditingMemoryStrategy(
        backend=InMemoryBackend(),
        config=SelfEditingMemoryConfig(),
    )


def _augment(
    strategy: object,
    agent_id: str = "agent-1",
) -> ToolRegistry:
    """Run the per-agent augmentation over an empty base registry."""
    return registry_with_memory_tools(
        ToolRegistry([]),
        strategy,  # type: ignore[arg-type]  # the None / wrong-type cases are the point
        NotBlankStr(agent_id),
    )


class TestSelfEditingToolRegistration:
    def test_all_six_tools_are_bound_for_the_agent(self) -> None:
        registry = _augment(_self_editing())

        assert {t.name for t in registry.all_tools()} == _EXPECTED

    def test_each_agent_gets_its_own_bound_tools(self) -> None:
        strategy = _self_editing()

        first = _augment(strategy, "agent-1")
        second = _augment(strategy, "agent-2")

        assert not set(first.all_tools()) & set(second.all_tools())

    def test_context_strategy_registers_none(self) -> None:
        """Their handler lives on the self-editing strategy alone."""
        registry = _augment(
            ContextInjectionStrategy(
                backend=InMemoryBackend(),
                config=MemoryRetrievalConfig(),
            ),
        )

        assert registry.all_tools() == ()

    def test_unwired_strategy_registers_none(self) -> None:
        assert _augment(None).all_tools() == ()

    def test_write_tool_exposes_the_supersedes_field(self) -> None:
        """Without it the agent cannot declare a replacement, and
        supersession is only ever acted on when declared."""
        registry = _augment(_self_editing())

        write = next(
            t for t in registry.all_tools() if t.name == "archival_memory_write"
        )
        schema = write.parameters_schema

        assert schema is not None
        properties = schema["properties"]
        assert isinstance(properties, dict)
        assert "supersedes" in properties
