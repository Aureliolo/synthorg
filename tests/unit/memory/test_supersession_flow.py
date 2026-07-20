"""End-to-end proof that a superseded belief stops being recalled.

The write gate deciding SUPERSEDE is worth nothing if retrieval keeps
surfacing the entry it retired. STALE (arXiv:2605.06527) shows a stale
and a fresh memory coexisting without arbitration is the production
failure mode, so this exercises the whole loop: write, replace, recall.
"""

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.backends.inmemory import InMemoryBackend
from synthorg.memory.models import MemoryQuery
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever import ContextInjectionStrategy
from synthorg.memory.self_editing import SelfEditingMemoryStrategy
from synthorg.memory.self_editing_models import SelfEditingMemoryConfig
from synthorg.memory.write_gate import SUPERSEDED_TAG
from tests._shared import recall_request

pytestmark = pytest.mark.unit

_AGENT = NotBlankStr("agent-1")
_OLD = "Deploys roll forward; never roll back the payment service."
_NEW = "Deploys roll back cleanly now that the payment service is versioned."


async def _strategy(backend: InMemoryBackend) -> SelfEditingMemoryStrategy:
    """Build a self-editing strategy over *backend*."""
    return SelfEditingMemoryStrategy(
        backend=backend,
        config=SelfEditingMemoryConfig(),
    )


async def _write(
    strategy: SelfEditingMemoryStrategy,
    content: str,
    *,
    supersedes: str | None = None,
) -> str:
    """Perform one archival write through the gate."""
    args: dict[str, object] = {
        "content": content,
        "category": MemoryCategory.SEMANTIC.value,
    }
    if supersedes is not None:
        args["supersedes"] = supersedes
    return await strategy.handle_tool_call("archival_memory_write", args, _AGENT)


async def _stored_id(backend: InMemoryBackend, content: str) -> str:
    """Find the id of the entry holding *content*."""
    entries = await backend.retrieve(_AGENT, MemoryQuery(limit=50))
    return next(str(e.id) for e in entries if e.content == content)


class TestSupersessionFlow:
    async def test_replaced_belief_is_retired_and_not_recalled(self) -> None:
        backend = InMemoryBackend()
        await backend.connect()
        strategy = await _strategy(backend)

        await _write(strategy, _OLD)
        old_id = await _stored_id(backend, _OLD)
        result = await _write(strategy, _NEW, supersedes=old_id)

        assert "replacing" in result

        # Retained for audit, so an explicit opt-in still sees it, while
        # ordinary recall (below) does not.
        archived = await backend.retrieve(
            _AGENT, MemoryQuery(limit=50, include_superseded=True)
        )
        retired = next(e for e in archived if str(e.id) == old_id)
        assert SUPERSEDED_TAG in retired.metadata.tags
        assert old_id not in {
            str(e.id) for e in await backend.retrieve(_AGENT, MemoryQuery(limit=50))
        }

        injection = ContextInjectionStrategy(
            backend=backend,
            config=MemoryRetrievalConfig(min_relevance=0.0),
        )
        messages = await injection.prepare_messages(
            recall_request(query="roll back the payment service", token_budget=2000)
        )
        content = "\n".join(m.content or "" for m in messages)

        assert _NEW in content
        assert "never roll back" not in content

    async def test_presupposing_query_does_not_resurface_the_stale_belief(
        self,
    ) -> None:
        """The case STALE shows collapses to 4% without a write-time gate.

        The query takes the outdated belief for granted rather than
        asking about it, which is where models stop noticing staleness.
        """
        backend = InMemoryBackend()
        await backend.connect()
        strategy = await _strategy(backend)

        await _write(strategy, _OLD)
        old_id = await _stored_id(backend, _OLD)
        await _write(strategy, _NEW, supersedes=old_id)

        injection = ContextInjectionStrategy(
            backend=backend,
            config=MemoryRetrievalConfig(min_relevance=0.0),
        )
        messages = await injection.prepare_messages(
            recall_request(
                query="Since we never roll back deploys, plan the payment fix",
                token_budget=2000,
            )
        )
        content = "\n".join(m.content or "" for m in messages)

        assert "never roll back" not in content

    async def test_duplicate_write_is_dropped(self) -> None:
        backend = InMemoryBackend()
        await backend.connect()
        strategy = await _strategy(backend)

        await _write(strategy, _OLD)
        result = await _write(strategy, _OLD)

        assert "Already remembered" in result
        entries = await backend.retrieve(_AGENT, MemoryQuery(limit=50))
        assert len([e for e in entries if e.content == _OLD]) == 1

    async def test_superseding_an_unknown_entry_is_refused(self) -> None:
        backend = InMemoryBackend()
        await backend.connect()
        strategy = await _strategy(backend)

        result = await _write(strategy, _NEW, supersedes="does-not-exist")

        assert result.lower().startswith("error")
        entries = await backend.retrieve(_AGENT, MemoryQuery(limit=50))
        assert entries == ()
