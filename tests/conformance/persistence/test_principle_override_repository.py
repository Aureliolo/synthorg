"""Conformance tests for ``PrincipleOverrideRepository``.

Runs once against SQLite and once against a real Postgres container
via the parametrised ``backend`` fixture so the two implementations
stay in lockstep.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.persistence.principle_override_protocol import PrincipleOverride
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _now() -> datetime:
    return datetime.now(UTC)


class TestPrincipleOverrideSave:
    async def test_first_save_inserts(self, backend: PersistenceBackend) -> None:
        repo = backend.principle_overrides
        now = _now()
        entity = PrincipleOverride(
            scope=NotBlankStr("planning.scope.alpha"),
            text=NotBlankStr("Restored text alpha"),
            restored_from=NotBlankStr("op-001"),
            created_at=now,
            updated_at=now,
        )
        await repo.save(entity)
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
        now_1 = _now() - timedelta(hours=1)
        entity_1 = PrincipleOverride(
            scope=scope,
            text=NotBlankStr("first"),
            restored_from=NotBlankStr("op-001"),
            created_at=now_1,
            updated_at=now_1,
        )
        await repo.save(entity_1)
        now_2 = _now()
        entity_2 = PrincipleOverride(
            scope=scope,
            text=NotBlankStr("second"),
            restored_from=NotBlankStr("op-002"),
            created_at=now_1,
            updated_at=now_2,
        )
        await repo.save(entity_2)
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
        now = _now()
        entity = PrincipleOverride(
            scope=scope,
            text=NotBlankStr("doomed"),
            restored_from=NotBlankStr("op-x"),
            created_at=now,
            updated_at=now,
        )
        await repo.save(entity)
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
        now = _now()
        for scope_str, text_str, op_str in [
            ("z.last", "zzz", "op-z"),
            ("a.first", "aaa", "op-a"),
            ("m.mid", "mmm", "op-m"),
        ]:
            entity = PrincipleOverride(
                scope=NotBlankStr(scope_str),
                text=NotBlankStr(text_str),
                restored_from=NotBlankStr(op_str),
                created_at=now,
                updated_at=now,
            )
            await repo.save(entity)
        rows = await repo.list_items()
        scopes = [r.scope for r in rows]
        # Ordering is alphabetical by scope.
        assert scopes == sorted(scopes)
        # All three present.
        assert {"a.first", "m.mid", "z.last"} <= set(scopes)
