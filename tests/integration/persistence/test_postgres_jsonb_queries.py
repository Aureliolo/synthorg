"""Integration tests for Postgres JSONB-native analytics queries.

Verifies the :class:`JsonbQueryCapability` implementation on
``PostgresAuditRepository`` using a real Postgres 18 container:

1. Functional correctness of ``query_jsonb_contains`` and
   ``query_jsonb_key_exists`` on ``audit_entries.matched_rules``
   (a JSONB array of rule name strings).
2. GIN index usage via ``EXPLAIN (ANALYZE, BUFFERS)``.
3. SQL-injection safety of path validation and column allowlist.

These tests require Docker (via testcontainers).
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.enums import ApprovalRiskLevel, ToolCategory
from synthorg.persistence.jsonb_capability import JsonbQueryCapability
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend
from synthorg.security.models import AuditEntry


def _make_audit_entry(  # noqa: PLR0913
    *,
    entry_id: str,
    matched_rules: tuple[str, ...] = (),
    agent_id: str = "agent-1",
    tool_name: str = "test-tool",
    action_type: str = "execute",
    timestamp: datetime | None = None,
) -> AuditEntry:
    return AuditEntry(
        id=entry_id,
        timestamp=timestamp or datetime.now(UTC),
        agent_id=agent_id,
        task_id="task-1",
        tool_name=tool_name,
        tool_category=ToolCategory.TERMINAL,
        action_type=action_type,
        arguments_hash="0" * 64,
        verdict="allow",
        risk_level=ApprovalRiskLevel.LOW,
        reason="test",
        matched_rules=matched_rules,
        evaluation_duration_ms=1.0,
        approval_id=None,
    )


# ── Capability protocol detection ──────────────────────────────


@pytest.mark.integration
class TestJsonbQueryCapability:
    """Postgres audit repository implements JsonbQueryCapability."""

    async def test_implements_protocol(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        repo = postgres_backend.audit_entries
        assert isinstance(repo, JsonbQueryCapability), (
            "PostgresAuditRepository must implement JsonbQueryCapability"
        )


# ── Containment (@>) operator ──────────────────────────────────


@pytest.mark.integration
class TestJsonbContains:
    """query_jsonb_contains uses the @> operator on JSONB arrays."""

    async def test_matches_array_element(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        repo = postgres_backend.audit_entries
        assert isinstance(repo, JsonbQueryCapability)

        for i in range(20):
            rules = ("rule-high",) if i % 2 == 0 else ("rule-low",)
            await repo.append(
                _make_audit_entry(
                    entry_id=f"entry-contains-{i}",
                    matched_rules=rules,
                ),
            )

        entries, total = await repo.query_jsonb_contains(
            "matched_rules",
            ["rule-high"],
            limit=50,
        )
        assert total == 10, f"expected 10 high-severity entries, got {total}"
        assert len(entries) == 10
        for e in entries:
            assert "rule-high" in e.matched_rules

    async def test_pagination(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        repo = postgres_backend.audit_entries
        assert isinstance(repo, JsonbQueryCapability)

        for i in range(10):
            await repo.append(
                _make_audit_entry(
                    entry_id=f"entry-page-{i}",
                    matched_rules=("rule-high",),
                ),
            )

        page1, total = await repo.query_jsonb_contains(
            "matched_rules",
            ["rule-high"],
            limit=5,
            offset=0,
        )
        assert total == 10
        assert len(page1) == 5

        page2, _ = await repo.query_jsonb_contains(
            "matched_rules",
            ["rule-high"],
            limit=5,
            offset=5,
        )
        assert len(page2) == 5
        page1_ids = {e.id for e in page1}
        page2_ids = {e.id for e in page2}
        assert not (page1_ids & page2_ids)

    async def test_time_window_filter(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        repo = postgres_backend.audit_entries
        assert isinstance(repo, JsonbQueryCapability)

        now = datetime.now(UTC)
        await repo.append(
            _make_audit_entry(
                entry_id="entry-old",
                matched_rules=("rule-high",),
                timestamp=now - timedelta(days=10),
            ),
        )
        await repo.append(
            _make_audit_entry(
                entry_id="entry-new",
                matched_rules=("rule-high",),
                timestamp=now,
            ),
        )

        entries, total = await repo.query_jsonb_contains(
            "matched_rules",
            ["rule-high"],
            since=now - timedelta(days=1),
            limit=50,
        )
        assert total == 1
        assert entries[0].id == "entry-new"


# ── Key existence (?) operator ──────────────────────────────────


@pytest.mark.integration
class TestJsonbKeyExists:
    """query_jsonb_key_exists uses ? operator.

    On a JSONB array column, ``matched_rules ? 'foo'`` returns true
    when 'foo' is one of the array elements -- equivalent semantics
    to ``@>``.  This test documents that behaviour.
    """

    async def test_matches_array_element(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        repo = postgres_backend.audit_entries
        assert isinstance(repo, JsonbQueryCapability)

        await repo.append(
            _make_audit_entry(
                entry_id="entry-key-1",
                matched_rules=("severity", "rule-a"),
            ),
        )
        await repo.append(
            _make_audit_entry(
                entry_id="entry-key-2",
                matched_rules=("rule-b",),
            ),
        )

        entries, total = await repo.query_jsonb_key_exists(
            "matched_rules",
            "severity",
            limit=50,
        )
        assert total == 1
        assert entries[0].id == "entry-key-1"


# ── GIN index usage via EXPLAIN ANALYZE ─────────────────────────


@pytest.mark.integration
class TestGinIndexUsage:
    """Verify the matched_rules GIN index is actually used."""

    async def test_explain_analyze_contains_uses_gin(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        repo = postgres_backend.audit_entries
        assert isinstance(repo, JsonbQueryCapability)

        for i in range(500):
            rules = ("rule-high", f"rule-{i}")
            await repo.append(
                _make_audit_entry(
                    entry_id=f"gin-entry-{i}",
                    matched_rules=rules,
                ),
            )

        pool = postgres_backend._pool
        assert pool is not None
        async with pool.connection() as conn:
            await conn.execute("ANALYZE audit_entries")
            async with conn.cursor() as cur:
                # Force index access to verify the GIN index is
                # actually usable.  At 500 rows the planner may
                # still prefer seq scan, but the test should verify
                # the index exists and can be used when needed.
                await cur.execute("SET LOCAL enable_seqscan = off")
                await cur.execute(
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) "
                    "SELECT id FROM audit_entries "
                    "WHERE matched_rules @> '[\"rule-high\"]'::jsonb",
                )
                rows = await cur.fetchall()
                plan_text = "\n".join(str(row[0]) for row in rows)

        assert "idx_ae_matched_rules_gin" in plan_text, (
            f"GIN index not used in plan:\n{plan_text}"
        )


# ── SQL-injection safety ────────────────────────────────────────


@pytest.mark.integration
class TestSqlInjectionSafety:
    """Verify that column allowlisting prevents injection."""

    async def test_rejects_unknown_column(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        repo = postgres_backend.audit_entries
        assert isinstance(repo, JsonbQueryCapability)

        with pytest.raises(ValueError, match="not allowed"):
            await repo.query_jsonb_contains(
                "password",
                ["foo"],
            )

    async def test_audit_entries_table_still_intact_after_injection_attempts(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        """After injection attempts, the table must still exist and be queryable."""
        repo = postgres_backend.audit_entries
        assert isinstance(repo, JsonbQueryCapability)

        with pytest.raises(ValueError, match="not allowed"):
            await repo.query_jsonb_contains("password", ["foo"])

        await repo.append(
            _make_audit_entry(
                entry_id="intact-1",
                matched_rules=("rule-check",),
            ),
        )
        from synthorg.persistence.audit_protocol import AuditFilterSpec

        entries = await repo.query(AuditFilterSpec(), limit=10)
        assert any(e.id == "intact-1" for e in entries)


# ── Parameter safety ────────────────────────────────────────────


@pytest.mark.integration
class TestParameterSafety:
    """Value parameters are safely parameterised, not string-interpolated."""

    async def test_injection_attempt_in_contains_value(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        repo = postgres_backend.audit_entries
        assert isinstance(repo, JsonbQueryCapability)

        # A malicious value should be treated as data, not SQL.
        entries, total = await repo.query_jsonb_contains(
            "matched_rules",
            ["'; DROP TABLE audit_entries; --"],
        )
        # No matching rows (the string is treated as literal data).
        assert total == 0
        assert len(entries) == 0

        # Verify the table still exists after the "injection attempt".
        pool = postgres_backend._pool
        assert pool is not None
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) FROM audit_entries")
            row = await cur.fetchone()
            assert row is not None
