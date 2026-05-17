"""Tests for InMemoryBackend."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.memory.backends.inmemory import InMemoryBackend
from synthorg.memory.errors import MemoryConnectionError, MemoryStoreError
from synthorg.memory.models import (
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)


@pytest.fixture
def backend() -> InMemoryBackend:
    """Disconnected InMemoryBackend with default config."""
    return InMemoryBackend()


@pytest.fixture
async def connected(backend: InMemoryBackend) -> InMemoryBackend:
    """Connected InMemoryBackend ready for CRUD."""
    await backend.connect()
    return backend


def _req(
    *,
    category: MemoryCategory = MemoryCategory.EPISODIC,
    namespace: str = "default",
    content: str = "test content",
    tags: tuple[str, ...] = (),
) -> MemoryStoreRequest:
    return MemoryStoreRequest(
        category=category,
        namespace=namespace,
        content=content,
        metadata=MemoryMetadata(tags=tags),
    )


# -- Protocol conformance --------------------------------------------


@pytest.mark.unit
class TestProtocol:
    def test_backend_name(self, backend: InMemoryBackend) -> None:
        assert backend.backend_name == "inmemory"

    def test_has_protocol_methods(self, backend: InMemoryBackend) -> None:
        for attr in (
            "connect",
            "disconnect",
            "health_check",
            "is_connected",
            "backend_name",
            "store",
            "retrieve",
            "get",
            "delete",
            "count",
        ):
            assert hasattr(backend, attr)


# -- Capabilities -----------------------------------------------------


@pytest.mark.unit
class TestCapabilities:
    def test_supported_categories(
        self,
        backend: InMemoryBackend,
    ) -> None:
        assert backend.supported_categories == frozenset(MemoryCategory)

    def test_supports_graph_false(
        self,
        backend: InMemoryBackend,
    ) -> None:
        assert backend.supports_graph is False

    def test_supports_temporal_true(
        self,
        backend: InMemoryBackend,
    ) -> None:
        assert backend.supports_temporal is True

    def test_supports_vector_search_false(
        self,
        backend: InMemoryBackend,
    ) -> None:
        assert backend.supports_vector_search is False

    def test_supports_shared_access_false(
        self,
        backend: InMemoryBackend,
    ) -> None:
        assert backend.supports_shared_access is False

    def test_max_memories_per_agent(
        self,
        backend: InMemoryBackend,
    ) -> None:
        assert backend.max_memories_per_agent == 10_000


# -- Lifecycle --------------------------------------------------------


@pytest.mark.unit
class TestLifecycle:
    async def test_not_connected_initially(
        self,
        backend: InMemoryBackend,
    ) -> None:
        assert backend.is_connected is False

    async def test_connect_sets_connected(
        self,
        backend: InMemoryBackend,
    ) -> None:
        await backend.connect()
        assert backend.is_connected is True

    async def test_connect_idempotent(
        self,
        backend: InMemoryBackend,
    ) -> None:
        await backend.connect()
        await backend.connect()
        assert backend.is_connected is True

    async def test_disconnect_clears_connected(
        self,
        connected: InMemoryBackend,
    ) -> None:
        await connected.disconnect()
        assert connected.is_connected is False

    async def test_disconnect_idempotent(
        self,
        backend: InMemoryBackend,
    ) -> None:
        await backend.disconnect()
        assert backend.is_connected is False

    async def test_health_check_when_connected(
        self,
        connected: InMemoryBackend,
    ) -> None:
        assert await connected.health_check() is True

    async def test_health_check_when_disconnected(
        self,
        backend: InMemoryBackend,
    ) -> None:
        assert await backend.health_check() is False


# -- Not-connected guard ----------------------------------------------


@pytest.mark.unit
class TestNotConnectedGuard:
    async def test_store_raises(
        self,
        backend: InMemoryBackend,
    ) -> None:
        with pytest.raises(MemoryConnectionError):
            await backend.store("agent-a", _req())

    async def test_retrieve_raises(
        self,
        backend: InMemoryBackend,
    ) -> None:
        with pytest.raises(MemoryConnectionError):
            await backend.retrieve("agent-a", MemoryQuery())

    async def test_get_raises(
        self,
        backend: InMemoryBackend,
    ) -> None:
        with pytest.raises(MemoryConnectionError):
            await backend.get("agent-a", "mem-1")

    async def test_delete_raises(
        self,
        backend: InMemoryBackend,
    ) -> None:
        with pytest.raises(MemoryConnectionError):
            await backend.delete("agent-a", "mem-1")

    async def test_count_raises(
        self,
        backend: InMemoryBackend,
    ) -> None:
        with pytest.raises(MemoryConnectionError):
            await backend.count("agent-a")


# -- Store + Get ------------------------------------------------------


@pytest.mark.unit
class TestStoreAndGet:
    async def test_store_returns_id(
        self,
        connected: InMemoryBackend,
    ) -> None:
        mid = await connected.store("agent-a", _req())
        assert isinstance(mid, str)
        assert mid != ""

    async def test_get_returns_entry(
        self,
        connected: InMemoryBackend,
    ) -> None:
        mid = await connected.store("agent-a", _req(content="hello"))
        entry = await connected.get("agent-a", mid)
        assert entry is not None
        assert entry.id == mid
        assert entry.agent_id == "agent-a"
        assert entry.content == "hello"
        assert entry.namespace == "default"
        assert entry.category == MemoryCategory.EPISODIC

    async def test_get_missing_returns_none(
        self,
        connected: InMemoryBackend,
    ) -> None:
        assert await connected.get("agent-a", "nonexistent") is None

    async def test_namespace_preserved(
        self,
        connected: InMemoryBackend,
    ) -> None:
        mid = await connected.store(
            "agent-a",
            _req(namespace="scratch"),
        )
        entry = await connected.get("agent-a", mid)
        assert entry is not None
        assert entry.namespace == "scratch"

    async def test_per_agent_isolation(
        self,
        connected: InMemoryBackend,
    ) -> None:
        mid = await connected.store("agent-a", _req())
        assert await connected.get("agent-b", mid) is None


# -- Delete -----------------------------------------------------------


@pytest.mark.unit
class TestDelete:
    async def test_delete_existing_returns_true(
        self,
        connected: InMemoryBackend,
    ) -> None:
        mid = await connected.store("agent-a", _req())
        assert await connected.delete("agent-a", mid) is True
        assert await connected.get("agent-a", mid) is None

    async def test_delete_missing_returns_false(
        self,
        connected: InMemoryBackend,
    ) -> None:
        assert await connected.delete("agent-a", "nope") is False


# -- Retrieve ---------------------------------------------------------


@pytest.mark.unit
class TestRetrieve:
    async def test_retrieve_all(
        self,
        connected: InMemoryBackend,
    ) -> None:
        await connected.store("a", _req(content="one"))
        await connected.store("a", _req(content="two"))
        result = await connected.retrieve("a", MemoryQuery())
        assert len(result) == 2

    async def test_retrieve_by_category(
        self,
        connected: InMemoryBackend,
    ) -> None:
        await connected.store(
            "a",
            _req(category=MemoryCategory.EPISODIC),
        )
        await connected.store(
            "a",
            _req(category=MemoryCategory.SEMANTIC),
        )
        result = await connected.retrieve(
            "a",
            MemoryQuery(
                categories=frozenset({MemoryCategory.SEMANTIC}),
            ),
        )
        assert len(result) == 1
        assert result[0].category == MemoryCategory.SEMANTIC

    async def test_retrieve_by_namespace(
        self,
        connected: InMemoryBackend,
    ) -> None:
        await connected.store(
            "a",
            _req(namespace="memories"),
        )
        await connected.store(
            "a",
            _req(namespace="scratch"),
        )
        result = await connected.retrieve(
            "a",
            MemoryQuery(namespaces=frozenset({"scratch"})),
        )
        assert len(result) == 1
        assert result[0].namespace == "scratch"

    async def test_retrieve_by_tags(
        self,
        connected: InMemoryBackend,
    ) -> None:
        await connected.store(
            "a",
            _req(tags=("important",)),
        )
        await connected.store("a", _req(tags=("trivial",)))
        result = await connected.retrieve(
            "a",
            MemoryQuery(tags=("important",)),
        )
        assert len(result) == 1

    async def test_retrieve_text_search(
        self,
        connected: InMemoryBackend,
    ) -> None:
        await connected.store("a", _req(content="the quick brown fox"))
        await connected.store("a", _req(content="lazy dog"))
        result = await connected.retrieve(
            "a",
            MemoryQuery(text="brown"),
        )
        assert len(result) == 1
        assert "brown" in result[0].content

    async def test_retrieve_respects_limit(
        self,
        connected: InMemoryBackend,
    ) -> None:
        for i in range(5):
            await connected.store("a", _req(content=f"item {i}"))
        result = await connected.retrieve(
            "a",
            MemoryQuery(limit=3),
        )
        assert len(result) == 3

    async def test_retrieve_ordered_by_created_at_desc(
        self,
        connected: InMemoryBackend,
    ) -> None:
        await connected.store("a", _req(content="first"))
        await connected.store("a", _req(content="second"))
        result = await connected.retrieve("a", MemoryQuery())
        assert result[0].content == "second"
        assert result[1].content == "first"

    @pytest.mark.parametrize(
        "category",
        list(MemoryCategory),
        ids=[c.value for c in MemoryCategory],
    )
    async def test_all_categories_storable(
        self,
        connected: InMemoryBackend,
        category: MemoryCategory,
    ) -> None:
        mid = await connected.store(
            "a",
            _req(category=category),
        )
        entry = await connected.get("a", mid)
        assert entry is not None
        assert entry.category == category


# -- Count ------------------------------------------------------------


@pytest.mark.unit
class TestCount:
    async def test_count_all(
        self,
        connected: InMemoryBackend,
    ) -> None:
        await connected.store("a", _req())
        await connected.store("a", _req())
        assert await connected.count("a") == 2

    async def test_count_by_category(
        self,
        connected: InMemoryBackend,
    ) -> None:
        await connected.store(
            "a",
            _req(category=MemoryCategory.EPISODIC),
        )
        await connected.store(
            "a",
            _req(category=MemoryCategory.SEMANTIC),
        )
        assert (
            await connected.count(
                "a",
                category=MemoryCategory.SEMANTIC,
            )
            == 1
        )

    async def test_count_empty(
        self,
        connected: InMemoryBackend,
    ) -> None:
        assert await connected.count("a") == 0


# -- Max memories per agent -------------------------------------------


@pytest.mark.unit
class TestMaxMemories:
    async def test_exceeding_limit_raises(self) -> None:
        b = InMemoryBackend(max_memories_per_agent=2)
        await b.connect()
        await b.store("a", _req(content="one"))
        await b.store("a", _req(content="two"))
        with pytest.raises(MemoryStoreError, match="limit"):
            await b.store("a", _req(content="three"))

    def test_invalid_limit_raises(self) -> None:
        with pytest.raises(ValueError, match="must be >= 1"):
            InMemoryBackend(max_memories_per_agent=0)


# -- Expiry -----------------------------------------------------------


@pytest.mark.unit
class TestExpiry:
    async def test_get_returns_none_for_expired_entry(
        self,
        connected: InMemoryBackend,
    ) -> None:
        past = datetime.now(tz=UTC) - timedelta(hours=1)
        mid = await connected.store(
            "a",
            _req(content="ephemeral"),
        )
        # Patch the stored entry's expires_at to the past.
        store = connected._store["a"]
        entry = store[mid]
        store[mid] = entry.model_copy(update={"expires_at": past})

        assert await connected.get("a", mid) is None

    async def test_retrieve_excludes_expired_entry(
        self,
        connected: InMemoryBackend,
    ) -> None:
        past = datetime.now(tz=UTC) - timedelta(hours=1)
        mid = await connected.store(
            "a",
            _req(content="expired"),
        )
        await connected.store("a", _req(content="alive"))
        store = connected._store["a"]
        entry = store[mid]
        store[mid] = entry.model_copy(update={"expires_at": past})

        result = await connected.retrieve("a", MemoryQuery())
        assert len(result) == 1
        assert result[0].content == "alive"

    async def test_count_excludes_expired_entry(
        self,
        connected: InMemoryBackend,
    ) -> None:
        past = datetime.now(tz=UTC) - timedelta(hours=1)
        mid = await connected.store(
            "a",
            _req(content="expired"),
        )
        await connected.store("a", _req(content="alive"))
        store = connected._store["a"]
        entry = store[mid]
        store[mid] = entry.model_copy(update={"expires_at": past})

        assert await connected.count("a") == 1


# -- Clear ------------------------------------------------------------


@pytest.mark.unit
class TestClear:
    async def test_clear_removes_all(
        self,
        connected: InMemoryBackend,
    ) -> None:
        await connected.store("a", _req())
        await connected.store("a", _req())
        removed = await connected.clear("a")
        assert removed == 2
        assert await connected.count("a") == 0

    async def test_clear_empty_returns_zero(
        self,
        connected: InMemoryBackend,
    ) -> None:
        assert await connected.clear("a") == 0

    async def test_clear_does_not_affect_other_agents(
        self,
        connected: InMemoryBackend,
    ) -> None:
        await connected.store("a", _req())
        await connected.store("b", _req())
        await connected.clear("a")
        assert await connected.count("b") == 1
