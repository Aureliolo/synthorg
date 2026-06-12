"""Tests for ConnectionCatalog.rebind_repository.

The catalog is built with an in-memory stub before persistence is
live; once ``persistence.connect()`` succeeds the API lifecycle hook
calls ``rebind_repository`` to swap the stub for the backend-bound
repo. Tests verify the swap is atomic, idempotent, and invalidates
the cache so subsequent reads observe the new backend.
"""

from collections.abc import Iterator

import pytest
from typeguard import suppress_type_checks

from synthorg.integrations.connections.catalog import ConnectionCatalog

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _suppress_typeguard_for_stub_repo() -> Iterator[None]:
    """Suppress typeguard: tests drive a ``_StubRepo`` repository double.

    The stub records its identity to verify ``rebind_repository`` swap / cache
    behaviour; it does not satisfy the full ``ConnectionRepository`` protocol,
    so the runtime check is suppressed.
    """
    with suppress_type_checks():
        yield


class _StubRepo:
    """Minimal ``ConnectionRepository`` stub recording its identity."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.list_all_calls = 0

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
        offset: int = 0,
    ) -> tuple[object, ...]:
        del limit, offset
        self.list_all_calls += 1
        return ()

    async def save(self, _connection: object) -> object:  # pragma: no cover
        raise NotImplementedError

    async def get(self, _name: str) -> object:  # pragma: no cover
        raise NotImplementedError

    async def delete(self, _name: str) -> bool:  # pragma: no cover
        raise NotImplementedError


class _StubSecretBackend:
    """No-op secret backend; rebind doesn't touch it."""

    async def store(  # pragma: no cover
        self,
        *args: object,
        **kwargs: object,
    ) -> object:
        raise NotImplementedError

    async def fetch(  # pragma: no cover
        self,
        *args: object,
        **kwargs: object,
    ) -> object:
        raise NotImplementedError

    async def delete(self, *args: object, **kwargs: object) -> bool:  # pragma: no cover
        raise NotImplementedError


def _make_catalog(initial_label: str) -> tuple[ConnectionCatalog, _StubRepo]:
    initial = _StubRepo(initial_label)
    catalog = ConnectionCatalog(
        repository=initial,  # type: ignore[arg-type]
        secret_backend=_StubSecretBackend(),  # type: ignore[arg-type]
    )
    return catalog, initial


class TestNameLockEviction:
    async def test_lock_evicted_when_idle(self) -> None:
        catalog, _initial = _make_catalog("stub")
        async with catalog._name_lock("conn-a"):
            assert "conn-a" in catalog._name_locks
        # Last (only) holder exited: the lock and its refcount are gone.
        assert catalog._name_locks == {}
        assert catalog._name_locks_refcounts == {}

    async def test_lock_retained_while_a_holder_remains(self) -> None:
        catalog, _initial = _make_catalog("stub")
        async with catalog._name_lock("conn-a"):
            # Simulate a second concurrent holder by bumping the refcount;
            # the lock must survive the first holder's exit.
            catalog._name_locks_refcounts["conn-a"] += 1
        assert "conn-a" in catalog._name_locks
        assert catalog._name_locks_refcounts == {"conn-a": 1}


class TestRebindRepository:
    async def test_swaps_repo_reference(self) -> None:
        catalog, _initial = _make_catalog("stub")
        replacement = _StubRepo("persistent")
        await catalog.rebind_repository(replacement)  # type: ignore[arg-type]
        # ``_repo`` is typed as ConnectionRepository so a ``StubRepo``
        # identity check is non-overlapping under strict mypy; tag the
        # field as Any locally for the assertion only.
        assert catalog._repo is replacement  # type: ignore[comparison-overlap]

    async def test_invalidates_cache(self) -> None:
        catalog, initial = _make_catalog("stub")
        # Force the cache to populate from the initial (stub) repo.
        await catalog._ensure_cache()
        # ``getattr`` defeats mypy's literal-narrowing so the
        # post-rebind invalidation read below is reachable; the field
        # really is mutated, mypy just can't observe it through the
        # rebind call.
        assert getattr(catalog, "_cache_valid") is True  # noqa: B009
        assert initial.list_all_calls == 1

        replacement = _StubRepo("persistent")
        await catalog.rebind_repository(replacement)  # type: ignore[arg-type]
        # Cache must be reset, otherwise subsequent reads would observe
        # connections that only exist in the stub.
        assert getattr(catalog, "_cache_valid") is False  # noqa: B009
        assert len(catalog._cache) == 0

    async def test_subsequent_read_uses_new_repo(self) -> None:
        catalog, initial = _make_catalog("stub")
        replacement = _StubRepo("persistent")
        await catalog.rebind_repository(replacement)  # type: ignore[arg-type]
        # Trigger a cache populate; should hit replacement, not initial.
        await catalog._ensure_cache()
        assert initial.list_all_calls == 0
        assert replacement.list_all_calls == 1

    async def test_double_rebind_is_safe(self) -> None:
        catalog, _initial = _make_catalog("stub")
        first = _StubRepo("first_replacement")
        second = _StubRepo("second_replacement")
        await catalog.rebind_repository(first)  # type: ignore[arg-type]
        await catalog.rebind_repository(second)  # type: ignore[arg-type]
        assert catalog._repo is second  # type: ignore[comparison-overlap]
