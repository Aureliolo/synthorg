"""The self-editing memory tools must reach a real agent's registry.

They were built and wired to nothing: six tool classes with a handler
behind them and no call site that registered them, so an agent running
under the self-editing strategy had no way to write its own memory.
"""

import pytest

from synthorg.memory.backends.inmemory import InMemoryBackend
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever import ContextInjectionStrategy
from synthorg.memory.self_editing import SelfEditingMemoryStrategy
from synthorg.memory.self_editing_models import SelfEditingMemoryConfig
from synthorg.tools.factory import _build_self_editing_memory_tools

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


class TestSelfEditingToolRegistration:
    def test_all_six_tools_are_built(self) -> None:
        tools = _build_self_editing_memory_tools(
            strategy=_self_editing(),
            agent_id="agent-1",
        )

        assert {t.name for t in tools} == _EXPECTED

    def test_context_strategy_registers_none(self) -> None:
        """Their handler lives on the self-editing strategy alone."""
        tools = _build_self_editing_memory_tools(
            strategy=ContextInjectionStrategy(
                backend=InMemoryBackend(),
                config=MemoryRetrievalConfig(),
            ),
            agent_id="agent-1",
        )

        assert tools == ()

    def test_unwired_strategy_registers_none(self) -> None:
        tools = _build_self_editing_memory_tools(strategy=None, agent_id="agent-1")

        assert tools == ()

    def test_write_tool_exposes_the_supersedes_field(self) -> None:
        """Without it the agent cannot declare a replacement, and
        supersession is only ever acted on when declared."""
        tools = _build_self_editing_memory_tools(
            strategy=_self_editing(),
            agent_id="agent-1",
        )

        write = next(t for t in tools if t.name == "archival_memory_write")
        schema = write.parameters_schema

        assert schema is not None
        properties = schema["properties"]
        assert isinstance(properties, dict)
        assert "supersedes" in properties
