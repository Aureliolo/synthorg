"""Conformance tests for ``SsrfViolationRepository`` (SQLite + Postgres).

Written from scratch for PST-1 -- no prior unit coverage existed.
Covers save/get/list_violations/update_status across both backends.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.security.ssrf_violation import SsrfViolation, SsrfViolationStatus
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 3, 15, 10, 0, 0, tzinfo=UTC)


def _violation(
    *,
    violation_id: str = "sv-001",
    hostname: str = "blocked.internal",
    status: SsrfViolationStatus = SsrfViolationStatus.PENDING,
    timestamp: datetime = _NOW,
) -> SsrfViolation:
    return SsrfViolation(
        id=as_uuid(violation_id),
        timestamp=timestamp,
        url=NotBlankStr(f"https://{hostname}/api/v1/tool"),
        hostname=NotBlankStr(hostname),
        port=443,
        resolved_ip=NotBlankStr("10.0.0.5"),
        blocked_range=NotBlankStr("10.0.0.0/8"),
        provider_name=NotBlankStr("example-provider"),
        status=status,
    )


class TestSsrfViolationRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        v = _violation()
        await backend.ssrf_violations.save(v)

        fetched = await backend.ssrf_violations.get(sid("sv-001"))
        assert fetched is not None
        assert fetched.hostname == "blocked.internal"
        assert fetched.status == SsrfViolationStatus.PENDING
        assert fetched.port == 443

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.ssrf_violations.get(NotBlankStr("ghost")) is None

    async def test_list_violations_all(self, backend: PersistenceBackend) -> None:
        await backend.ssrf_violations.save(_violation(violation_id="a"))
        await backend.ssrf_violations.save(
            _violation(violation_id="b", timestamp=_NOW + timedelta(minutes=1)),
        )

        rows = await backend.ssrf_violations.list_violations()
        ids = [v.id for v in rows]
        assert as_uuid("a") in ids
        assert as_uuid("b") in ids

    async def test_list_violations_orders_desc(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.ssrf_violations.save(
            _violation(violation_id="older", timestamp=_NOW),
        )
        await backend.ssrf_violations.save(
            _violation(violation_id="newer", timestamp=_NOW + timedelta(minutes=5)),
        )

        rows = await backend.ssrf_violations.list_violations()
        ordered = [v.id for v in rows if v.id in {as_uuid("older"), as_uuid("newer")}]
        assert ordered == [as_uuid("newer"), as_uuid("older")]

    async def test_list_violations_filters_by_status(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.ssrf_violations.save(_violation(violation_id="p1"))
        await backend.ssrf_violations.save(_violation(violation_id="p2"))

        await backend.ssrf_violations.update_status(
            sid("p1"),
            status=SsrfViolationStatus.ALLOWED,
            resolved_by=NotBlankStr("alice"),
            resolved_at=_NOW + timedelta(minutes=1),
        )

        pending = await backend.ssrf_violations.list_violations(
            status=SsrfViolationStatus.PENDING,
        )
        pending_ids = {v.id for v in pending}
        assert as_uuid("p2") in pending_ids
        assert as_uuid("p1") not in pending_ids

        allowed = await backend.ssrf_violations.list_violations(
            status=SsrfViolationStatus.ALLOWED,
        )
        allowed_ids = {v.id for v in allowed}
        assert as_uuid("p1") in allowed_ids

    async def test_list_violations_rejects_non_positive_limit(
        self, backend: PersistenceBackend
    ) -> None:
        with pytest.raises(ValueError, match=r"(?i)limit"):
            await backend.ssrf_violations.list_violations(limit=0)

    async def test_update_status_pending_to_denied(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.ssrf_violations.save(_violation(violation_id="u1"))

        updated = await backend.ssrf_violations.update_status(
            sid("u1"),
            status=SsrfViolationStatus.DENIED,
            resolved_by=NotBlankStr("bob"),
            resolved_at=_NOW + timedelta(minutes=5),
        )
        assert updated is True

        fetched = await backend.ssrf_violations.get(sid("u1"))
        assert fetched is not None
        assert fetched.status == SsrfViolationStatus.DENIED
        assert fetched.resolved_by == "bob"

    async def test_update_status_noop_when_already_resolved(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.ssrf_violations.save(_violation(violation_id="u2"))
        await backend.ssrf_violations.update_status(
            sid("u2"),
            status=SsrfViolationStatus.ALLOWED,
            resolved_by=NotBlankStr("alice"),
            resolved_at=_NOW,
        )

        # Second resolution attempt should be a no-op.
        second = await backend.ssrf_violations.update_status(
            sid("u2"),
            status=SsrfViolationStatus.DENIED,
            resolved_by=NotBlankStr("bob"),
            resolved_at=_NOW + timedelta(minutes=10),
        )
        assert second is False

    async def test_update_status_rejects_pending_target(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.ssrf_violations.save(_violation(violation_id="u3"))
        with pytest.raises(ValueError, match=r"(?i)pending"):
            await backend.ssrf_violations.update_status(
                sid("u3"),
                status=SsrfViolationStatus.PENDING,
                resolved_by=NotBlankStr("alice"),
                resolved_at=_NOW,
            )
