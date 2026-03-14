"""Tests for the in-memory audit log."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.enums import ApprovalRiskLevel, ToolCategory
from synthorg.security.audit import AuditLog
from synthorg.security.models import AuditEntry, AuditVerdictStr

pytestmark = pytest.mark.timeout(30)


# ── Helpers ───────────────────────────────────────────────────────


def _make_entry(  # noqa: PLR0913
    *,
    entry_id: str = "entry-1",
    agent_id: str | None = "agent-a",
    tool_name: str = "test-tool",
    verdict: AuditVerdictStr = "allow",
    risk_level: ApprovalRiskLevel = ApprovalRiskLevel.LOW,
    timestamp: datetime | None = None,
    action_type: str = "code:read",
) -> AuditEntry:
    return AuditEntry(
        id=entry_id,
        timestamp=timestamp or datetime.now(UTC),
        agent_id=agent_id,
        tool_name=tool_name,
        tool_category=ToolCategory.FILE_SYSTEM,
        action_type=action_type,
        arguments_hash="a" * 64,
        verdict=verdict,
        risk_level=risk_level,
        reason="Test entry",
        evaluation_duration_ms=1.0,
    )


# ── Tests ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestAuditLogRecordAndCount:
    """Basic record and count operations."""

    def test_initial_count_zero(self) -> None:
        log = AuditLog()
        assert log.count() == 0

    def test_record_increments_count(self) -> None:
        log = AuditLog()
        log.record(_make_entry(entry_id="e1"))

        assert log.count() == 1

    def test_multiple_records(self) -> None:
        log = AuditLog()
        for i in range(5):
            log.record(_make_entry(entry_id=f"e{i}"))

        assert log.count() == 5


@pytest.mark.unit
class TestAuditLogEviction:
    """Max entries eviction behavior."""

    def test_eviction_at_max_entries(self) -> None:
        log = AuditLog(max_entries=3)
        for i in range(5):
            log.record(_make_entry(entry_id=f"e{i}"))

        assert log.count() == 3
        # Only the last 3 entries should remain.
        entry_ids = [e.id for e in log.entries]
        assert entry_ids == ["e2", "e3", "e4"]

    def test_oldest_entries_evicted_first(self) -> None:
        log = AuditLog(max_entries=2)
        log.record(_make_entry(entry_id="first"))
        log.record(_make_entry(entry_id="second"))
        log.record(_make_entry(entry_id="third"))

        entries = log.entries
        assert len(entries) == 2
        assert entries[0].id == "second"
        assert entries[1].id == "third"


@pytest.mark.unit
class TestAuditLogMaxEntriesValidation:
    """Validation of max_entries parameter."""

    def test_zero_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="max_entries must be >= 1"):
            AuditLog(max_entries=0)

    def test_negative_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="max_entries must be >= 1"):
            AuditLog(max_entries=-5)

    def test_one_is_valid(self) -> None:
        log = AuditLog(max_entries=1)
        log.record(_make_entry(entry_id="e1"))
        log.record(_make_entry(entry_id="e2"))
        assert log.count() == 1
        assert log.entries[0].id == "e2"


@pytest.mark.unit
class TestAuditLogEntries:
    """The entries property returns oldest-first."""

    def test_entries_oldest_first(self) -> None:
        log = AuditLog()
        now = datetime.now(UTC)
        log.record(
            _make_entry(
                entry_id="old",
                timestamp=now - timedelta(hours=2),
            ),
        )
        log.record(
            _make_entry(
                entry_id="mid",
                timestamp=now - timedelta(hours=1),
            ),
        )
        log.record(
            _make_entry(
                entry_id="new",
                timestamp=now,
            ),
        )

        entries = log.entries
        assert entries[0].id == "old"
        assert entries[1].id == "mid"
        assert entries[2].id == "new"

    def test_entries_returns_tuple(self) -> None:
        log = AuditLog()
        log.record(_make_entry(entry_id="e1"))
        assert isinstance(log.entries, tuple)

    def test_entries_empty_when_no_records(self) -> None:
        log = AuditLog()
        assert log.entries == ()


@pytest.mark.unit
class TestAuditLogQueryFilters:
    """Query with various filters."""

    def _populate(self, log: AuditLog) -> None:
        """Add diverse entries for filter testing."""
        now = datetime.now(UTC)
        log.record(
            _make_entry(
                entry_id="e1",
                agent_id="agent-a",
                tool_name="tool-x",
                verdict="allow",
                risk_level=ApprovalRiskLevel.LOW,
                timestamp=now - timedelta(hours=3),
            ),
        )
        log.record(
            _make_entry(
                entry_id="e2",
                agent_id="agent-b",
                tool_name="tool-y",
                verdict="deny",
                risk_level=ApprovalRiskLevel.HIGH,
                timestamp=now - timedelta(hours=2),
            ),
        )
        log.record(
            _make_entry(
                entry_id="e3",
                agent_id="agent-a",
                tool_name="tool-x",
                verdict="deny",
                risk_level=ApprovalRiskLevel.CRITICAL,
                timestamp=now - timedelta(hours=1),
            ),
        )
        log.record(
            _make_entry(
                entry_id="e4",
                agent_id="agent-c",
                tool_name="tool-z",
                verdict="escalate",
                risk_level=ApprovalRiskLevel.HIGH,
                timestamp=now,
            ),
        )

    def test_filter_by_agent_id(self) -> None:
        log = AuditLog()
        self._populate(log)

        results = log.query(agent_id="agent-a")

        ids = [e.id for e in results]
        assert set(ids) == {"e1", "e3"}

    def test_filter_by_tool_name(self) -> None:
        log = AuditLog()
        self._populate(log)

        results = log.query(tool_name="tool-y")

        assert len(results) == 1
        assert results[0].id == "e2"

    def test_filter_by_verdict(self) -> None:
        log = AuditLog()
        self._populate(log)

        results = log.query(verdict="deny")

        ids = [e.id for e in results]
        assert set(ids) == {"e2", "e3"}

    def test_filter_by_risk_level(self) -> None:
        log = AuditLog()
        self._populate(log)

        results = log.query(risk_level=ApprovalRiskLevel.HIGH)

        ids = [e.id for e in results]
        assert set(ids) == {"e2", "e4"}

    def test_filter_by_since(self) -> None:
        log = AuditLog()
        self._populate(log)

        cutoff = datetime.now(UTC) - timedelta(hours=1, minutes=30)
        results = log.query(since=cutoff)

        ids = [e.id for e in results]
        assert set(ids) == {"e3", "e4"}

    def test_combined_filters(self) -> None:
        log = AuditLog()
        self._populate(log)

        results = log.query(
            agent_id="agent-a",
            verdict="deny",
        )

        assert len(results) == 1
        assert results[0].id == "e3"

    def test_query_returns_newest_first(self) -> None:
        log = AuditLog()
        self._populate(log)

        results = log.query()

        assert results[0].id == "e4"
        assert results[-1].id == "e1"

    def test_query_limit(self) -> None:
        log = AuditLog()
        self._populate(log)

        results = log.query(limit=2)

        assert len(results) == 2

    def test_no_matches(self) -> None:
        log = AuditLog()
        self._populate(log)

        results = log.query(agent_id="nonexistent")

        assert results == ()

    def test_query_limit_zero_raises_value_error(self) -> None:
        log = AuditLog()
        self._populate(log)

        with pytest.raises(ValueError, match="limit must be >= 1"):
            log.query(limit=0)


@pytest.mark.unit
class TestAuditLogTotalRecorded:
    """Total recorded count tracks all entries, including evicted."""

    def test_total_recorded_equals_count_without_eviction(self) -> None:
        log = AuditLog()
        for i in range(5):
            log.record(_make_entry(entry_id=f"e{i}"))

        assert log.total_recorded == 5
        assert log.count() == 5

    def test_total_recorded_exceeds_count_after_eviction(self) -> None:
        log = AuditLog(max_entries=3)
        for i in range(10):
            log.record(_make_entry(entry_id=f"e{i}"))

        assert log.total_recorded == 10
        assert log.count() == 3

    def test_total_recorded_zero_initially(self) -> None:
        log = AuditLog()
        assert log.total_recorded == 0
