"""Tests for audit log query controller."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from litestar.testing import TestClient

from synthorg.core.enums import ApprovalRiskLevel, ToolCategory
from synthorg.persistence._shared import parse_iso_utc
from synthorg.security.audit import AuditLog
from synthorg.security.models import AuditEntry, AuditVerdictStr
from tests.unit.api.conftest import make_auth_headers

_HEADERS = make_auth_headers("ceo")
_DUMMY_HASH = "a" * 64


def _make_entry(  # noqa: PLR0913
    *,
    entry_id: str = "e-1",
    agent_id: str | None = "agent-a",
    tool_name: str = "code_write",
    action_type: str = "code:write",
    verdict: AuditVerdictStr = "allow",
    timestamp: datetime | None = None,
) -> AuditEntry:
    return AuditEntry(
        id=entry_id,
        timestamp=timestamp or datetime(2026, 4, 1, tzinfo=UTC),
        agent_id=agent_id,
        tool_name=tool_name,
        tool_category=ToolCategory.CODE_EXECUTION,
        action_type=action_type,
        arguments_hash=_DUMMY_HASH,
        verdict=verdict,
        risk_level=ApprovalRiskLevel.LOW,
        reason="test-reason",
        evaluation_duration_ms=1.0,
    )


@pytest.mark.unit
class TestAuditController:
    def test_empty_audit_log(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get(
            "/api/v1/security/audit",
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == []

    def test_returns_entries_paginated(
        self,
        test_client: TestClient[Any],
        audit_log: AuditLog,
    ) -> None:
        for i in range(3):
            audit_log.record(_make_entry(entry_id=f"e-{i}"))
        resp = test_client.get(
            "/api/v1/security/audit",
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 3

    @pytest.mark.parametrize(
        ("field", "match_val", "other_val"),
        [
            ("agent_id", "alice", "bob"),
            ("verdict", "deny", "allow"),
            ("action_type", "deploy:production", "code:write"),
            ("tool_name", "file_read", "code_write"),
        ],
    )
    def test_filter_by_field(
        self,
        test_client: TestClient[Any],
        audit_log: AuditLog,
        field: str,
        match_val: str,
        other_val: str,
    ) -> None:
        audit_log.record(
            _make_entry(entry_id="e-1", **{field: match_val}),  # type: ignore[arg-type]
        )
        audit_log.record(
            _make_entry(entry_id="e-2", **{field: other_val}),  # type: ignore[arg-type]
        )
        resp = test_client.get(
            "/api/v1/security/audit",
            params={field: match_val},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0][field] == match_val

    def test_filter_by_since_until(
        self,
        test_client: TestClient[Any],
        audit_log: AuditLog,
    ) -> None:
        t1 = datetime(2026, 4, 1, tzinfo=UTC)
        t2 = t1 + timedelta(hours=1)
        t3 = t1 + timedelta(hours=2)
        audit_log.record(_make_entry(entry_id="e-1", timestamp=t1))
        audit_log.record(_make_entry(entry_id="e-2", timestamp=t2))
        audit_log.record(_make_entry(entry_id="e-3", timestamp=t3))
        resp = test_client.get(
            "/api/v1/security/audit",
            params={
                "since": t1.isoformat(),
                "until": t2.isoformat(),
            },
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        # Filter is applied -- only entries within [t1, t2] come back.
        # Compare as datetimes (not strings) so the wire's ``Z`` suffix
        # and ``isoformat()``'s ``+00:00`` suffix don't string-compare
        # to different values for the same instant.  Also assert the
        # exact set of ids so an empty result (regression: filter
        # eats every row) cannot pass the loop vacuously.
        assert isinstance(body["data"], list)
        assert len(body["data"]) == 2
        assert {entry["id"] for entry in body["data"]} == {"e-1", "e-2"}
        for entry in body["data"]:
            # Require ``timestamp`` strictly so a wire-contract regression
            # that drops the field (or renames it) cannot be masked by
            # the previous ``created_at`` fallback.
            assert "timestamp" in entry
            entry_dt = parse_iso_utc(entry["timestamp"])
            assert t1 <= entry_dt <= t2

    def test_pagination_offset_limit(
        self,
        test_client: TestClient[Any],
        audit_log: AuditLog,
    ) -> None:
        for i in range(5):
            audit_log.record(_make_entry(entry_id=f"e-{i}"))
        # Walk two pages via cursor to reach offset 2.
        resp1 = test_client.get(
            "/api/v1/security/audit",
            params={"limit": 2},
            headers=_HEADERS,
        )
        assert resp1.status_code == 200
        cursor = resp1.json()["pagination"]["next_cursor"]
        assert cursor is not None
        resp = test_client.get(
            "/api/v1/security/audit",
            params={"limit": 2, "cursor": cursor},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["limit"] == 2
        assert len(body["data"]) == 2

    def test_rejects_inverted_time_window(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """since > until is a validation failure (HTTP 422)."""
        t1 = datetime(2026, 4, 1, tzinfo=UTC)
        t2 = t1 - timedelta(hours=1)
        resp = test_client.get(
            "/api/v1/security/audit",
            params={"since": t1.isoformat(), "until": t2.isoformat()},
            headers=_HEADERS,
        )
        assert resp.status_code == 422

    def test_combined_filters_and(
        self,
        test_client: TestClient[Any],
        audit_log: AuditLog,
    ) -> None:
        """Multiple filters are AND-combined."""
        audit_log.record(
            _make_entry(
                entry_id="e-1",
                agent_id="alice",
                verdict="allow",
            ),
        )
        audit_log.record(
            _make_entry(
                entry_id="e-2",
                agent_id="alice",
                verdict="deny",
            ),
        )
        audit_log.record(
            _make_entry(
                entry_id="e-3",
                agent_id="bob",
                verdict="allow",
            ),
        )
        resp = test_client.get(
            "/api/v1/security/audit",
            params={"agent_id": "alice", "verdict": "deny"},
            headers=_HEADERS,
        )
        body = resp.json()
        assert body["data"][0]["id"] == "e-2"
