"""Tests for CompositeBackend -- namespace routing + prefixed IDs."""

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.memory.backends.composite import (
    CompositeBackend,
    CompositeBackendConfig,
)
from synthorg.memory.backends.inmemory import InMemoryBackend
from synthorg.memory.errors import (
    MemoryConfigError,
    MemoryConnectionError,
    MemoryRetrievalError,
)
from synthorg.memory.models import (
    MemoryQuery,
    MemoryStoreRequest,
)


def _req(
    *,
    category: MemoryCategory = MemoryCategory.EPISODIC,
    namespace: str = "default",
    content: str = "test content",
) -> MemoryStoreRequest:
    return MemoryStoreRequest(
        category=category,
        namespace=namespace,
        content=content,
    )


@pytest.fixture
def durable() -> InMemoryBackend:
    """Simulates durable backend (like Mem0)."""
    return InMemoryBackend()


@pytest.fixture
def session() -> InMemoryBackend:
    """Simulates session-scoped backend."""
    return InMemoryBackend()


@pytest.fixture
def config() -> CompositeBackendConfig:
    return CompositeBackendConfig(
        routes={
            "memories": "durable",
            "preferences": "durable",
            "scratch": "session",
            "working": "session",
        },
        default="session",
    )


@pytest.fixture
def composite(
    durable: InMemoryBackend,
    session: InMemoryBackend,
    config: CompositeBackendConfig,
) -> CompositeBackend:
    return CompositeBackend(
        children={"durable": durable, "session": session},
        config=config,
    )


@pytest.fixture
async def connected(
    composite: CompositeBackend,
) -> CompositeBackend:
    await composite.connect()
    return composite


# -- Constructor validation -------------------------------------------


@pytest.mark.unit
class TestConstructor:
    def test_unknown_route_backend_raises(self) -> None:
        with pytest.raises(MemoryConfigError, match="unknown backend"):
            CompositeBackend(
                children={"a": InMemoryBackend()},
                config=CompositeBackendConfig(
                    routes={"ns": "nonexistent"},
                    default="a",
                ),
            )

    def test_unknown_default_backend_raises(self) -> None:
        with pytest.raises(MemoryConfigError, match="unknown backend"):
            CompositeBackend(
                children={"a": InMemoryBackend()},
                config=CompositeBackendConfig(default="missing"),
            )


# -- Protocol conformance --------------------------------------------


@pytest.mark.unit
class TestProtocol:
    def test_backend_name(self, composite: CompositeBackend) -> None:
        assert composite.backend_name == "composite"

    def test_has_protocol_methods(
        self,
        composite: CompositeBackend,
    ) -> None:
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
            assert hasattr(composite, attr)


# -- Lifecycle --------------------------------------------------------


@pytest.mark.unit
class TestLifecycle:
    async def test_connect_all_children(
        self,
        composite: CompositeBackend,
        durable: InMemoryBackend,
        session: InMemoryBackend,
    ) -> None:
        await composite.connect()
        assert durable.is_connected
        assert session.is_connected
        assert composite.is_connected

    async def test_disconnect_all_children(
        self,
        connected: CompositeBackend,
    ) -> None:
        await connected.disconnect()
        assert not connected.is_connected

    async def test_health_check_all_healthy(
        self,
        connected: CompositeBackend,
    ) -> None:
        assert await connected.health_check() is True

    async def test_health_check_unhealthy_child(
        self,
        connected: CompositeBackend,
        durable: InMemoryBackend,
    ) -> None:
        await durable.disconnect()
        assert await connected.health_check() is False


# -- Namespace routing ------------------------------------------------


@pytest.mark.unit
class TestNamespaceRouting:
    async def test_store_to_memories_routes_to_durable(
        self,
        connected: CompositeBackend,
        durable: InMemoryBackend,
        session: InMemoryBackend,
    ) -> None:
        mid = await connected.store("a", _req(namespace="memories"))
        assert mid.startswith("durable:")
        # Verify it's in the durable backend.
        assert await durable.count("a") == 1
        assert await session.count("a") == 0

    async def test_store_to_scratch_routes_to_session(
        self,
        connected: CompositeBackend,
        durable: InMemoryBackend,
        session: InMemoryBackend,
    ) -> None:
        mid = await connected.store("a", _req(namespace="scratch"))
        assert mid.startswith("session:")
        assert await session.count("a") == 1
        assert await durable.count("a") == 0

    async def test_unknown_namespace_routes_to_default(
        self,
        connected: CompositeBackend,
        session: InMemoryBackend,
    ) -> None:
        mid = await connected.store("a", _req(namespace="unknown"))
        assert mid.startswith("session:")
        assert await session.count("a") == 1

    async def test_orthogonality_same_category_different_ns(
        self,
        connected: CompositeBackend,
        durable: InMemoryBackend,
        session: InMemoryBackend,
    ) -> None:
        """EPISODIC memories can live in both durable and session."""
        cat = MemoryCategory.EPISODIC
        await connected.store(
            "a",
            _req(category=cat, namespace="memories"),
        )
        await connected.store(
            "a",
            _req(category=cat, namespace="scratch"),
        )
        assert await durable.count("a") == 1
        assert await session.count("a") == 1


# -- Prefixed IDs ----------------------------------------------------


@pytest.mark.unit
class TestPrefixedIds:
    async def test_store_returns_prefixed_id(
        self,
        connected: CompositeBackend,
    ) -> None:
        mid = await connected.store("a", _req(namespace="memories"))
        assert ":" in mid
        prefix, _ = mid.split(":", 1)
        assert prefix == "durable"

    async def test_get_by_prefixed_id(
        self,
        connected: CompositeBackend,
    ) -> None:
        mid = await connected.store(
            "a",
            _req(namespace="memories", content="hello"),
        )
        entry = await connected.get("a", mid)
        assert entry is not None
        assert entry.content == "hello"
        assert entry.id == mid

    async def test_delete_by_prefixed_id(
        self,
        connected: CompositeBackend,
    ) -> None:
        mid = await connected.store("a", _req(namespace="scratch"))
        assert await connected.delete("a", mid) is True
        assert await connected.get("a", mid) is None

    async def test_get_missing_returns_none(
        self,
        connected: CompositeBackend,
    ) -> None:
        assert await connected.get("a", "session:nonexistent") is None

    async def test_delete_missing_returns_false(
        self,
        connected: CompositeBackend,
    ) -> None:
        assert await connected.delete("a", "session:nope") is False

    async def test_unknown_prefix_raises(
        self,
        connected: CompositeBackend,
    ) -> None:
        with pytest.raises(MemoryRetrievalError, match="Unknown backend"):
            await connected.get("a", "badprefix:123")

    async def test_no_separator_raises(
        self,
        connected: CompositeBackend,
    ) -> None:
        with pytest.raises(
            MemoryRetrievalError,
            match="missing backend prefix",
        ):
            await connected.get("a", "nocolon")


# -- Retrieve fan-out -------------------------------------------------


@pytest.mark.unit
class TestRetrieveFanout:
    async def test_retrieve_specific_namespace(
        self,
        connected: CompositeBackend,
    ) -> None:
        await connected.store(
            "a",
            _req(namespace="memories", content="durable"),
        )
        await connected.store(
            "a",
            _req(namespace="scratch", content="session"),
        )
        result = await connected.retrieve(
            "a",
            MemoryQuery(namespaces=frozenset({"memories"})),
        )
        assert len(result) == 1
        assert result[0].content == "durable"

    async def test_retrieve_all_namespaces(
        self,
        connected: CompositeBackend,
    ) -> None:
        await connected.store(
            "a",
            _req(namespace="memories", content="d"),
        )
        await connected.store(
            "a",
            _req(namespace="scratch", content="s"),
        )
        result = await connected.retrieve("a", MemoryQuery())
        assert len(result) == 2

    async def test_retrieve_respects_limit(
        self,
        connected: CompositeBackend,
    ) -> None:
        for i in range(5):
            await connected.store(
                "a",
                _req(namespace="memories", content=f"item {i}"),
            )
        result = await connected.retrieve(
            "a",
            MemoryQuery(limit=3),
        )
        assert len(result) == 3

    async def test_retrieve_entries_have_prefixed_ids(
        self,
        connected: CompositeBackend,
    ) -> None:
        await connected.store(
            "a",
            _req(namespace="memories", content="test"),
        )
        result = await connected.retrieve("a", MemoryQuery())
        assert len(result) == 1
        assert result[0].id.startswith("durable:")

    async def test_retrieve_degrades_gracefully_on_child_error(
        self,
        connected: CompositeBackend,
        durable: InMemoryBackend,
        session: InMemoryBackend,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Results from healthy child are returned when another child raises."""
        await connected.store(
            "a",
            _req(namespace="memories", content="durable-item"),
        )
        await connected.store(
            "a",
            _req(namespace="scratch", content="session-item"),
        )

        # Break the session backend's retrieve method.
        async def _broken_retrieve(
            *args: object,
            **kwargs: object,
        ) -> list[object]:
            msg = "simulated backend failure"
            raise Exception(msg)  # noqa: TRY002

        monkeypatch.setattr(session, "retrieve", _broken_retrieve)

        # Should NOT raise -- graceful degradation.
        result = await connected.retrieve("a", MemoryQuery())
        # Only the durable backend's entry survives.
        assert len(result) == 1
        contents = {e.content for e in result}
        assert "durable-item" in contents

    async def test_retrieve_multiple_namespaces(
        self,
        connected: CompositeBackend,
    ) -> None:
        await connected.store(
            "a",
            _req(namespace="memories", content="d"),
        )
        await connected.store(
            "a",
            _req(namespace="scratch", content="s"),
        )
        await connected.store(
            "a",
            _req(namespace="working", content="w"),
        )
        result = await connected.retrieve(
            "a",
            MemoryQuery(
                namespaces=frozenset({"memories", "scratch"}),
            ),
        )
        # "scratch" and "working" both route to "session" backend,
        # but "working" namespace is not in the query filter, so the
        # child backend's post-filter will exclude it.
        contents = {e.content for e in result}
        assert "d" in contents
        assert "s" in contents


# -- Count ------------------------------------------------------------


@pytest.mark.unit
class TestCount:
    async def test_count_all_backends(
        self,
        connected: CompositeBackend,
    ) -> None:
        await connected.store("a", _req(namespace="memories"))
        await connected.store("a", _req(namespace="scratch"))
        assert await connected.count("a") == 2

    async def test_count_by_category(
        self,
        connected: CompositeBackend,
    ) -> None:
        await connected.store(
            "a",
            _req(
                namespace="memories",
                category=MemoryCategory.SEMANTIC,
            ),
        )
        await connected.store(
            "a",
            _req(
                namespace="scratch",
                category=MemoryCategory.WORKING,
            ),
        )
        assert (
            await connected.count(
                "a",
                category=MemoryCategory.SEMANTIC,
            )
            == 1
        )


# -- Not-connected guard ----------------------------------------------


@pytest.mark.unit
class TestNotConnectedGuard:
    async def test_store_raises(
        self,
        composite: CompositeBackend,
    ) -> None:
        with pytest.raises(MemoryConnectionError):
            await composite.store("a", _req())

    async def test_retrieve_raises(
        self,
        composite: CompositeBackend,
    ) -> None:
        with pytest.raises(MemoryConnectionError):
            await composite.retrieve("a", MemoryQuery())


# -- Capabilities aggregation ----------------------------------------


@pytest.mark.unit
class TestCapabilities:
    def test_supported_categories(
        self,
        composite: CompositeBackend,
    ) -> None:
        assert composite.supported_categories == frozenset(
            MemoryCategory,
        )

    def test_supports_vector_search(
        self,
        composite: CompositeBackend,
    ) -> None:
        # Both children are InMemoryBackend (no vector search).
        assert composite.supports_vector_search is False

    def test_supports_temporal(
        self,
        composite: CompositeBackend,
    ) -> None:
        assert composite.supports_temporal is True

    def test_max_memories_per_agent(
        self,
        composite: CompositeBackend,
    ) -> None:
        assert composite.max_memories_per_agent == 10_000
