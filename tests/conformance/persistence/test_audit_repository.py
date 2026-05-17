"""Parametrized conformance tests for ``AuditRepository``.

Runs against both SQLite and Postgres via the ``backend`` fixture so
SQLite-vs-Postgres divergence is caught on every commit.  Audit
entries are append-only: ``append`` plus ``query`` plus ``purge_before``.
The SQLite backend stores ``timestamp`` as TEXT (ISO-8601,
UTC-normalised at write time) and ``matched_rules`` as TEXT holding
``json.dumps(...)``; the Postgres backend uses TIMESTAMPTZ and JSONB
respectively.  The tests assert that both produce identical Pydantic
models from the protocol surface, and that the shared helpers in
``synthorg.persistence._shared.audit`` handle serialisation round-trip,
duplicate-id classification, and UTC timestamp normalisation.
"""

from datetime import UTC, datetime, timedelta, timezone
from typing import Literal

import pytest
from pydantic import ValidationError

from synthorg.core.enums import ApprovalRiskLevel, ToolCategory
from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.persistence.audit_protocol import AuditFilterSpec
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.security.models import (
    AuditEntry,
    AuditVerdictStr,
    EvaluationConfidence,
)

pytestmark = pytest.mark.integration


_FAKE_HASH = "a" * 64


def _entry(  # noqa: PLR0913
    entry_id: str = "audit-1",
    *,
    timestamp: datetime | None = None,
    agent_id: str | None = "agent-1",
    action_type: str = "fs:read",
    verdict: AuditVerdictStr = "allow",
    risk_level: ApprovalRiskLevel = ApprovalRiskLevel.LOW,
    matched_rules: tuple[str, ...] = ("rule-allowlist",),
    approval_id: str | None = None,
) -> AuditEntry:
    """Build an ``AuditEntry`` with sensible defaults."""
    ts = timestamp or datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    return AuditEntry(
        id=entry_id,
        timestamp=ts,
        agent_id=agent_id,
        task_id="task-1",
        tool_name="filesystem.read",
        tool_category=ToolCategory.FILE_SYSTEM,
        action_type=action_type,
        arguments_hash=_FAKE_HASH,
        verdict=verdict,
        risk_level=risk_level,
        reason="conformance test",
        matched_rules=matched_rules,
        evaluation_duration_ms=1.5,
        confidence=EvaluationConfidence.HIGH,
        approval_id=approval_id,
    )


class TestAuditRepositoryConformance:
    async def test_save_and_query_round_trip(self, backend: PersistenceBackend) -> None:
        entry = _entry(matched_rules=("rule-a", "rule-b"))
        await backend.audit_entries.append(entry)

        rows = await backend.audit_entries.query(AuditFilterSpec())
        assert len(rows) == 1
        fetched = rows[0]
        assert fetched.id == entry.id
        assert fetched.agent_id == "agent-1"
        assert fetched.matched_rules == ("rule-a", "rule-b")
        assert fetched.timestamp.tzinfo is not None
        assert fetched.timestamp == entry.timestamp

    async def test_query_empty_returns_empty_tuple(
        self, backend: PersistenceBackend
    ) -> None:
        rows = await backend.audit_entries.query(AuditFilterSpec())
        assert rows == ()

    async def test_save_duplicate_raises(self, backend: PersistenceBackend) -> None:
        entry = _entry()
        await backend.audit_entries.append(entry)
        with pytest.raises(DuplicateRecordError):
            await backend.audit_entries.append(entry)

    async def test_non_utc_timestamp_normalised_on_round_trip(
        self, backend: PersistenceBackend
    ) -> None:
        offset_tz = timezone(timedelta(hours=5))
        entry = _entry(
            entry_id="offset-tz",
            timestamp=datetime(2026, 4, 24, 17, 0, tzinfo=offset_tz),
        )
        await backend.audit_entries.append(entry)

        rows = await backend.audit_entries.query(AuditFilterSpec())
        assert len(rows) == 1
        # After UTC normalisation the instant is preserved but tzinfo is UTC.
        expected = datetime(2026, 4, 24, 12, 0, tzinfo=UTC)
        assert rows[0].timestamp == expected
        assert rows[0].timestamp.tzinfo == UTC

    async def test_query_filter_by_agent_id(self, backend: PersistenceBackend) -> None:
        await backend.audit_entries.append(_entry("a", agent_id="alice"))
        await backend.audit_entries.append(_entry("b", agent_id="bob"))

        rows = await backend.audit_entries.query(AuditFilterSpec(agent_id="alice"))
        assert {r.id for r in rows} == {"a"}

    async def test_query_filter_by_action_type(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.audit_entries.append(_entry("read", action_type="fs:read"))
        await backend.audit_entries.append(_entry("write", action_type="fs:write"))

        rows = await backend.audit_entries.query(
            AuditFilterSpec(action_type="fs:write"),
        )
        assert {r.id for r in rows} == {"write"}

    async def test_query_filter_by_verdict(self, backend: PersistenceBackend) -> None:
        await backend.audit_entries.append(_entry("a", verdict="allow"))
        await backend.audit_entries.append(_entry("d", verdict="deny"))

        rows = await backend.audit_entries.query(AuditFilterSpec(verdict="deny"))
        assert {r.id for r in rows} == {"d"}

    async def test_query_filter_by_risk_level(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.audit_entries.append(
            _entry("low", risk_level=ApprovalRiskLevel.LOW),
        )
        await backend.audit_entries.append(
            _entry("crit", risk_level=ApprovalRiskLevel.CRITICAL),
        )

        rows = await backend.audit_entries.query(
            AuditFilterSpec(risk_level=ApprovalRiskLevel.CRITICAL),
        )
        assert {r.id for r in rows} == {"crit"}

    async def test_query_time_range(self, backend: PersistenceBackend) -> None:
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        t1 = datetime(2026, 1, 2, tzinfo=UTC)
        t2 = datetime(2026, 1, 3, tzinfo=UTC)
        for tag, ts in (("e0", t0), ("e1", t1), ("e2", t2)):
            await backend.audit_entries.append(_entry(tag, timestamp=ts))

        rows = await backend.audit_entries.query(AuditFilterSpec(since=t1, until=t1))
        assert {r.id for r in rows} == {"e1"}

        rows = await backend.audit_entries.query(AuditFilterSpec(since=t1))
        assert {r.id for r in rows} == {"e1", "e2"}

        rows = await backend.audit_entries.query(AuditFilterSpec(until=t1))
        assert {r.id for r in rows} == {"e0", "e1"}

    async def test_query_orders_timestamp_desc(
        self, backend: PersistenceBackend
    ) -> None:
        for i in range(3):
            await backend.audit_entries.append(
                _entry(
                    f"e{i}",
                    timestamp=datetime(2026, 1, i + 1, tzinfo=UTC),
                ),
            )

        rows = await backend.audit_entries.query(AuditFilterSpec())
        assert [r.id for r in rows] == ["e2", "e1", "e0"]

    async def test_query_limit_respected(self, backend: PersistenceBackend) -> None:
        # Append 5 entries with distinct timestamps, then assert that
        # query(limit=N) returns the EXACT N newest entries in
        # descending-timestamp order.  A length-only assertion would
        # silently pass against a backend that returned the wrong rows
        # or the wrong ordering.
        for i in range(5):
            await backend.audit_entries.append(
                _entry(
                    f"e{i}",
                    timestamp=datetime(2026, 1, i + 1, tzinfo=UTC),
                ),
            )

        rows = await backend.audit_entries.query(AuditFilterSpec(), limit=2)
        assert len(rows) == 2
        assert [r.id for r in rows] == ["e4", "e3"]

    async def test_query_zero_limit_raises(self, backend: PersistenceBackend) -> None:
        with pytest.raises(QueryError):
            await backend.audit_entries.query(AuditFilterSpec(), limit=0)

    async def test_query_until_before_since_raises(
        self, backend: PersistenceBackend
    ) -> None:
        # An inverted window is rejected at the filter-spec boundary
        # (model_validator) rather than deferred to query() time, so
        # callers cannot construct an invalid spec at all.
        with pytest.raises(ValidationError):
            AuditFilterSpec(
                since=datetime(2026, 1, 2, tzinfo=UTC),
                until=datetime(2026, 1, 1, tzinfo=UTC),
            )

    async def test_purge_before_removes_old_rows(
        self, backend: PersistenceBackend
    ) -> None:
        old = _entry("old", timestamp=datetime(2025, 12, 1, tzinfo=UTC))
        recent = _entry("recent", timestamp=datetime(2026, 1, 15, tzinfo=UTC))
        await backend.audit_entries.append(old)
        await backend.audit_entries.append(recent)

        cutoff = datetime(2026, 1, 1, tzinfo=UTC)
        deleted = await backend.audit_entries.purge_before(cutoff)
        assert deleted == 1

        rows = await backend.audit_entries.query(AuditFilterSpec())
        assert {r.id for r in rows} == {"recent"}

    async def test_purge_before_no_match_returns_zero(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.audit_entries.append(
            _entry("recent", timestamp=datetime(2026, 1, 15, tzinfo=UTC)),
        )
        deleted = await backend.audit_entries.purge_before(
            datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert deleted == 0

    async def test_matched_rules_order_preserved(
        self, backend: PersistenceBackend
    ) -> None:
        # JSONB on Postgres, TEXT(json.dumps) on SQLite -- assert the
        # tuple round-trips identically in the original order.
        rules: tuple[Literal["rule-a", "rule-b", "rule-c"], ...] = (
            "rule-a",
            "rule-b",
            "rule-c",
        )
        await backend.audit_entries.append(_entry(matched_rules=rules))
        rows = await backend.audit_entries.query(AuditFilterSpec())
        assert len(rows) == 1
        assert rows[0].matched_rules == rules

    async def test_jsonb_query_postgres_only(
        self, backend: PersistenceBackend
    ) -> None:  # lint-allow: dual-backend-parity -- JSONB @> is Postgres-only
        # Postgres-only: GIN-backed @> containment.  SQLite has no
        # equivalent; skip on that arm so the contract is documented in
        # the conformance file rather than in a separate Postgres suite.
        if backend.backend_name != "postgres":
            pytest.skip("JSONB containment query is Postgres-only")

        await backend.audit_entries.append(
            _entry("e1", matched_rules=("rule-allowlist", "rule-x")),
        )
        await backend.audit_entries.append(
            _entry("e2", matched_rules=("rule-y",)),
        )

        rows, total = await backend.audit_entries.query_jsonb_contains(  # type: ignore[attr-defined]
            "matched_rules",
            ["rule-x"],
        )
        assert total == 1
        assert {r.id for r in rows} == {"e1"}
