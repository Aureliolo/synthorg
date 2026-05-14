"""Conformance tests for ``PrincipleOverrideRepository``.

Runs once against SQLite and once against a real Postgres container
via the parametrised ``backend`` fixture so the two implementations
stay in lockstep.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _now() -> datetime:
    return datetime.now(UTC)


class TestPrincipleOverrideSave:
    async def test_first_save_inserts(self, backend: PersistenceBackend) -> None:
        repo = backend.principle_overrides
        await repo.save(
            NotBlankStr("planning.scope.alpha"),
            NotBlankStr("Restored text alpha"),
            restored_from=NotBlankStr("op-001"),
        )
        loaded = await repo.get(NotBlankStr("planning.scope.alpha"))
        assert loaded is not None
        assert loaded.text == "Restored text alpha"
        assert loaded.restored_from == "op-001"

    async def test_second_save_updates_in_place(
        self,
        backend: PersistenceBackend,
    ) -> None:
        repo = backend.principle_overrides
        scope = NotBlankStr("planning.scope.beta")
        await repo.save(
            scope,
            NotBlankStr("first"),
            restored_from=NotBlankStr("op-001"),
            now=_now() - timedelta(hours=1),
        )
        await repo.save(
            scope,
            NotBlankStr("second"),
            restored_from=NotBlankStr("op-002"),
            now=_now(),
        )
        loaded = await repo.get(scope)
        assert loaded is not None
        assert loaded.text == "second"
        assert loaded.restored_from == "op-002"
        # updated_at advances; created_at also from the second save in our
        # ON CONFLICT path (excluded.updated_at). What matters is that
        # the row was upserted, not duplicated.
        rows = await repo.list_items()
        scopes = [r.scope for r in rows if r.scope == scope]
        assert len(scopes) == 1


class TestPrincipleOverrideGet:
    async def test_missing_returns_none(
        self,
        backend: PersistenceBackend,
    ) -> None:
        loaded = await backend.principle_overrides.get(NotBlankStr("does.not.exist"))
        assert loaded is None


class TestPrincipleOverrideDelete:
    async def test_delete_existing_returns_true(
        self,
        backend: PersistenceBackend,
    ) -> None:
        repo = backend.principle_overrides
        scope = NotBlankStr("planning.scope.delete-me")
        await repo.save(
            scope,
            NotBlankStr("doomed"),
            restored_from=NotBlankStr("op-x"),
        )
        deleted = await repo.delete(scope)
        assert deleted is True
        assert await repo.get(scope) is None

    async def test_delete_missing_returns_false(
        self,
        backend: PersistenceBackend,
    ) -> None:
        deleted = await backend.principle_overrides.delete(
            NotBlankStr("no.such.scope"),
        )
        assert deleted is False


class TestPrincipleOverrideList:
    async def test_list_returns_all_ordered_by_scope(
        self,
        backend: PersistenceBackend,
    ) -> None:
        repo = backend.principle_overrides
        await repo.save(
            NotBlankStr("z.last"),
            NotBlankStr("zzz"),
            restored_from=NotBlankStr("op-z"),
        )
        await repo.save(
            NotBlankStr("a.first"),
            NotBlankStr("aaa"),
            restored_from=NotBlankStr("op-a"),
        )
        await repo.save(
            NotBlankStr("m.mid"),
            NotBlankStr("mmm"),
            restored_from=NotBlankStr("op-m"),
        )
        rows = await repo.list_items()
        scopes = [r.scope for r in rows]
        # Ordering is alphabetical by scope.
        assert scopes == sorted(scopes)
        # All three present.
        assert {"a.first", "m.mid", "z.last"} <= set(scopes)
