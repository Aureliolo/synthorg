"""Conformance tests for ``OrgFactRepository``.

Covers the MVCC publish/retract model plus the append-only operation
log against both SQLite and Postgres.  Replaces the
``tests/unit/memory/org/test_store.py`` file deleted during issue
#1457's consolidation -- that file exercised the SQLite-only store
directly; this one runs the same behaviour against every backend
behind the shared ``backend`` fixture.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.seniority import SeniorityLevel
from synthorg.memory.enums import OrgFactCategory
from synthorg.memory.org.models import OrgFact, OrgFactAuthor
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration


def _human_author() -> OrgFactAuthor:
    return OrgFactAuthor(is_human=True)


def _agent_author(agent_id: str = "agent_1") -> OrgFactAuthor:
    return OrgFactAuthor(
        agent_id=NotBlankStr(agent_id),
        seniority=SeniorityLevel.MID,
        is_human=False,
    )


def _fact(
    fact_id: str = "fact_1",
    content: str = "We ship on Tuesdays.",
    *,
    category: OrgFactCategory = OrgFactCategory.CORE_POLICY,
    tags: tuple[str, ...] = (),
    at: datetime | None = None,
) -> OrgFact:
    return OrgFact(
        id=NotBlankStr(fact_id),
        content=NotBlankStr(content),
        category=category,
        tags=tuple(NotBlankStr(t) for t in tags),
        author=_human_author(),
        created_at=at or datetime.now(UTC),
    )


class TestOrgFactRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await backend.org_facts.save(_fact())
        fetched = await backend.org_facts.get(NotBlankStr("fact_1"))
        assert fetched is not None
        assert fetched.content == "We ship on Tuesdays."
        assert fetched.category == OrgFactCategory.CORE_POLICY

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.org_facts.get(NotBlankStr("ghost")) is None

    async def test_query_by_category(self, backend: PersistenceBackend) -> None:
        await backend.org_facts.save(
            _fact("p1", "policy one", category=OrgFactCategory.CORE_POLICY),
        )
        await backend.org_facts.save(
            _fact("c1", "convention one", category=OrgFactCategory.CONVENTION),
        )
        rows = await backend.org_facts.query(
            categories=frozenset({OrgFactCategory.CORE_POLICY}),
        )
        assert all(f.category == OrgFactCategory.CORE_POLICY for f in rows)
        assert "p1" in {f.id for f in rows}
        assert "c1" not in {f.id for f in rows}

    async def test_query_by_text_substring(self, backend: PersistenceBackend) -> None:
        await backend.org_facts.save(
            _fact("hit1", "We ship on Tuesdays."),
        )
        await backend.org_facts.save(
            _fact("miss1", "Unrelated fact."),
        )
        rows = await backend.org_facts.query(text="ship on Tues")
        ids = {f.id for f in rows}
        assert "hit1" in ids
        assert "miss1" not in ids

    async def test_list_by_category(self, backend: PersistenceBackend) -> None:
        await backend.org_facts.save(
            _fact("lp1", category=OrgFactCategory.CORE_POLICY),
        )
        await backend.org_facts.save(
            _fact("lp2", category=OrgFactCategory.CORE_POLICY),
        )
        await backend.org_facts.save(
            _fact("other", category=OrgFactCategory.CONVENTION),
        )
        rows = await backend.org_facts.list_by_category(OrgFactCategory.CORE_POLICY)
        ids = {f.id for f in rows}
        assert {"lp1", "lp2"} <= ids
        assert "other" not in ids

    async def test_list_by_category_pagination(
        self, backend: PersistenceBackend
    ) -> None:
        for i in range(4):
            await backend.org_facts.save(
                _fact(
                    f"pg_{i}",
                    category=OrgFactCategory.CORE_POLICY,
                ),
            )

        # Pull the full ordered list to anchor pagination assertions
        # against the actual sort order rather than guessing.
        full = await backend.org_facts.list_by_category(
            OrgFactCategory.CORE_POLICY,
        )
        full_ids = [f.id for f in full]
        assert {"pg_0", "pg_1", "pg_2", "pg_3"} <= set(full_ids)

        page = await backend.org_facts.list_by_category(
            OrgFactCategory.CORE_POLICY,
            limit=2,
            offset=1,
        )
        assert [f.id for f in page] == full_ids[1:3]

    async def test_query_offset(self, backend: PersistenceBackend) -> None:
        for i in range(3):
            await backend.org_facts.save(
                _fact(f"qoff_{i}", "Common ship statement."),
            )

        page = await backend.org_facts.query(
            text="ship",
            limit=10,
            offset=1,
        )

        # All three match; offset=1 drops the first result.
        assert len(page) == 2

    async def test_delete_retracts_fact(self, backend: PersistenceBackend) -> None:
        await backend.org_facts.save(_fact("doomed"))
        deleted = await backend.org_facts.delete(
            NotBlankStr("doomed"),
            author=_agent_author(),
        )
        assert deleted is True
        # A retracted fact is no longer active -- get returns None.
        assert await backend.org_facts.get(NotBlankStr("doomed")) is None

    async def test_delete_missing_returns_false(
        self, backend: PersistenceBackend
    ) -> None:
        deleted = await backend.org_facts.delete(
            NotBlankStr("never_existed"),
            author=_agent_author(),
        )
        assert deleted is False

    async def test_snapshot_at_captures_active_state(
        self, backend: PersistenceBackend
    ) -> None:
        before = datetime.now(UTC) - timedelta(minutes=1)
        await backend.org_facts.save(_fact("snap1", at=before))
        # Query snapshot immediately after -- the fact must be active.
        now = datetime.now(UTC) + timedelta(seconds=5)
        rows = await backend.org_facts.snapshot_at(now)
        assert any(r.fact_id == "snap1" for r in rows)

    async def test_operation_log_tracks_publish_and_retract(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.org_facts.save(_fact("log1"))
        await backend.org_facts.delete(
            NotBlankStr("log1"),
            author=_agent_author(),
        )
        log = await backend.org_facts.get_operation_log(NotBlankStr("log1"))
        ops = [e.operation_type for e in log]
        assert "PUBLISH" in ops
        assert "RETRACT" in ops
        versions = [e.version for e in log]
        assert versions == sorted(versions), "versions must be monotonic"
