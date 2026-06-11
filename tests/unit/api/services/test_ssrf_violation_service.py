"""Unit tests for :class:`SsrfViolationService`.

Mirrors the structure of ``test_project_service.py``: a fake in-memory
repository implements the :class:`SsrfViolationRepository` protocol,
the service is constructed against it, and tests assert behaviour +
the audit event fired on each mutation.

Audit-trail coverage is the security-critical contract here -- the
WHO + WHEN of a resolution decision must be captured at the service
layer (the underlying repo is intentionally silent on mutations).
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import override

import pytest
import structlog

from synthorg.api.services.ssrf_violation_service import SsrfViolationService
from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability.events.api import (
    API_SSRF_VIOLATION_FETCH_FAILED,
    API_SSRF_VIOLATION_LISTED,
)
from synthorg.observability.events.security import (
    SECURITY_SSRF_VIOLATION_ALLOWED,
    SECURITY_SSRF_VIOLATION_DENIED,
    SECURITY_SSRF_VIOLATION_RECORDED,
    SECURITY_SSRF_VIOLATION_RESOLUTION_FAILED,
)
from synthorg.security.ssrf_violation import SsrfViolation, SsrfViolationStatus
from tests._shared import as_uuid

pytestmark = pytest.mark.unit


class _FakeSsrfViolationRepo:
    """In-memory ``SsrfViolationRepository`` used as a test stub."""

    def __init__(self) -> None:
        self._rows: dict[str, SsrfViolation] = {}

    async def save(self, violation: SsrfViolation) -> None:
        if str(violation.id) in self._rows:
            msg = f"duplicate violation: {violation.id!s}"
            raise DuplicateRecordError(msg)
        self._rows[str(violation.id)] = violation

    async def get(self, violation_id: NotBlankStr) -> SsrfViolation | None:
        return self._rows.get(violation_id)

    async def list_violations(
        self,
        *,
        status: SsrfViolationStatus | None = None,
        limit: int = 100,
    ) -> tuple[SsrfViolation, ...]:
        if limit < 1:
            msg = "limit must be positive"
            raise ValueError(msg)
        rows: Iterable[SsrfViolation] = sorted(
            self._rows.values(), key=lambda v: v.timestamp, reverse=True
        )
        if status is not None:
            rows = [v for v in rows if v.status == status]
        return tuple(list(rows)[:limit])

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[SsrfViolation, ...]:
        ordered = sorted(self._rows.values(), key=lambda v: v.id)
        return tuple(ordered[offset : offset + limit])

    async def delete(self, violation_id: NotBlankStr) -> bool:
        return self._rows.pop(violation_id, None) is not None

    async def update_status(
        self,
        violation_id: NotBlankStr,
        *,
        status: SsrfViolationStatus,
        resolved_by: NotBlankStr,
        resolved_at: datetime,
    ) -> bool:
        if status == SsrfViolationStatus.PENDING:
            msg = "Cannot transition a violation back to PENDING"
            raise ValueError(msg)
        existing = self._rows.get(violation_id)
        if existing is None or existing.status != SsrfViolationStatus.PENDING:
            return False
        self._rows[violation_id] = existing.model_copy(
            update={
                "status": status,
                "resolved_by": resolved_by,
                "resolved_at": resolved_at,
            }
        )
        return True


def _make_violation(
    *,
    violation_id: str = "sv-1",
    hostname: str = "example.invalid",
    port: int = 443,
    provider_name: str | None = "example-provider",
) -> SsrfViolation:
    return SsrfViolation(
        id=as_uuid(violation_id),
        timestamp=datetime(2026, 4, 25, 10, 0, 0, tzinfo=UTC),
        url=NotBlankStr(f"https://{hostname}:{port}/v1/models"),
        hostname=NotBlankStr(hostname),
        port=port,
        resolved_ip=None,
        blocked_range=None,
        provider_name=NotBlankStr(provider_name) if provider_name else None,
    )


async def test_record_persists_and_emits_audit() -> None:
    """``record`` saves the violation and fires ``SECURITY_SSRF_VIOLATION_RECORDED``.

    Asserts the structured kwargs (``violation_id``, ``hostname``,
    ``port``, ``provider_name``, ``status``) so a future refactor that
    drops a field from the audit payload is caught.
    """
    repo = _FakeSsrfViolationRepo()
    service = SsrfViolationService(repo=repo)
    violation = _make_violation()

    with structlog.testing.capture_logs() as logs:
        await service.record(violation)

    fetched = await repo.get(str(violation.id))
    assert fetched == violation

    events = [log for log in logs if log["event"] == SECURITY_SSRF_VIOLATION_RECORDED]
    assert len(events) == 1, f"expected one event in {logs}"
    event = events[0]
    assert event["violation_id"] == str(violation.id)
    assert event["hostname"] == violation.hostname
    assert event["port"] == violation.port
    assert event["provider_name"] == violation.provider_name
    assert event["status"] == SsrfViolationStatus.PENDING.value


async def test_record_propagates_duplicate_error() -> None:
    """Duplicate persistence errors propagate; only the warning audit fires.

    CLAUDE.md `## Logging`: "All error paths must log at WARNING or
    ERROR with context before raising."  The success-shape INFO event
    must NOT fire (no full payload), but a WARNING with ``error_type``
    is required so incident triage can correlate the failure.
    """
    repo = _FakeSsrfViolationRepo()
    service = SsrfViolationService(repo=repo)
    violation = _make_violation()
    await service.record(violation)

    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(DuplicateRecordError),
    ):
        await service.record(violation)

    audits = [log for log in logs if log["event"] == SECURITY_SSRF_VIOLATION_RECORDED]
    info_audits = [log for log in audits if log.get("log_level") == "info"]
    warning_audits = [log for log in audits if log.get("log_level") == "warning"]
    assert info_audits == [], (
        "the success-shape INFO event must NOT fire when save() raises -- "
        f"got {info_audits}"
    )
    assert len(warning_audits) == 1
    assert warning_audits[0]["error_type"] == "DuplicateRecordError"
    assert warning_audits[0]["violation_id"] == str(violation.id)


async def test_get_returns_persisted_violation() -> None:
    """``get`` returns whatever the underlying repo returns."""
    repo = _FakeSsrfViolationRepo()
    service = SsrfViolationService(repo=repo)
    violation = _make_violation()
    await service.record(violation)

    fetched = await service.get(str(violation.id))
    assert fetched == violation


async def test_get_returns_none_for_missing() -> None:
    """``get`` propagates ``None`` from the repo for missing rows."""
    repo = _FakeSsrfViolationRepo()
    service = SsrfViolationService(repo=repo)
    assert await service.get(NotBlankStr("nope")) is None


async def test_list_emits_audit_with_count_and_filter() -> None:
    """``list_violations`` audits the result count and the status filter."""
    repo = _FakeSsrfViolationRepo()
    service = SsrfViolationService(repo=repo)
    await service.record(_make_violation(violation_id="sv-a"))
    await service.record(_make_violation(violation_id="sv-b"))

    with structlog.testing.capture_logs() as logs:
        rows = await service.list_violations(status=SsrfViolationStatus.PENDING)

    assert len(rows) == 2
    listed = [log for log in logs if log["event"] == API_SSRF_VIOLATION_LISTED]
    assert len(listed) == 1
    assert listed[0]["count"] == 2
    assert listed[0]["status_filter"] == SsrfViolationStatus.PENDING.value


async def test_list_audits_status_filter_none() -> None:
    """``status_filter`` carries ``None`` when no filter applied."""
    repo = _FakeSsrfViolationRepo()
    service = SsrfViolationService(repo=repo)

    with structlog.testing.capture_logs() as logs:
        rows = await service.list_violations()

    assert rows == ()
    listed = [log for log in logs if log["event"] == API_SSRF_VIOLATION_LISTED]
    assert len(listed) == 1
    assert listed[0]["status_filter"] is None
    assert listed[0]["count"] == 0


class _RaisingReadRepo(_FakeSsrfViolationRepo):
    """Stub that raises ``QueryError`` on every read.

    Pins the read-side failure audits: ``API_SSRF_VIOLATION_FETCH_FAILED``
    on ``get`` and the WARNING form of ``API_SSRF_VIOLATION_LISTED`` on
    ``list_violations``. The success-path tests only cover the happy
    branch, so these stubs guard the error-path emissions against
    silent regressions.
    """

    @override
    async def get(self, violation_id: NotBlankStr) -> SsrfViolation | None:
        msg = "boom"
        raise QueryError(msg)

    @override
    async def list_violations(
        self,
        *,
        status: SsrfViolationStatus | None = None,
        limit: int = 100,
    ) -> tuple[SsrfViolation, ...]:
        msg = "boom"
        raise QueryError(msg)


async def test_get_failure_emits_fetch_failed_audit() -> None:
    """``get()`` failures fire ``API_SSRF_VIOLATION_FETCH_FAILED``.

    Distinct from list-level failures so endpoint-specific alerting
    can route single-fetch errors separately.
    """
    service = SsrfViolationService(repo=_RaisingReadRepo())

    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(QueryError),
    ):
        await service.get(NotBlankStr("sv-x"))

    audits = [log for log in logs if log["event"] == API_SSRF_VIOLATION_FETCH_FAILED]
    assert len(audits) == 1
    assert audits[0]["log_level"] == "warning"
    assert audits[0]["error_type"] == "QueryError"
    assert audits[0]["violation_id"] == "sv-x"
    # The list-level event must NOT fire on a single-fetch failure.
    assert not any(log["event"] == API_SSRF_VIOLATION_LISTED for log in logs)


async def test_list_failure_emits_warning_listed_audit() -> None:
    """``list_violations()`` failures fire WARNING ``API_SSRF_VIOLATION_LISTED``.

    Carries ``error_type`` + ``status_filter`` + ``limit`` for incident
    triage -- success-path emits INFO with ``count``, failure-path
    emits WARNING with the request shape.
    """
    service = SsrfViolationService(repo=_RaisingReadRepo())

    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(QueryError),
    ):
        await service.list_violations(status=SsrfViolationStatus.PENDING, limit=50)

    audits = [log for log in logs if log["event"] == API_SSRF_VIOLATION_LISTED]
    warnings = [log for log in audits if log.get("log_level") == "warning"]
    info_audits = [log for log in audits if log.get("log_level") == "info"]
    assert info_audits == [], (
        f"INFO success-shape audit must not fire when list raises -- got {info_audits}"
    )
    assert len(warnings) == 1
    assert warnings[0]["error_type"] == "QueryError"
    assert warnings[0]["status_filter"] == SsrfViolationStatus.PENDING.value
    assert warnings[0]["limit"] == 50


async def test_update_status_emits_audit_on_success() -> None:
    """Successful status transitions audit with WHO+WHEN."""
    repo = _FakeSsrfViolationRepo()
    service = SsrfViolationService(repo=repo)
    violation = _make_violation()
    await service.record(violation)
    resolved_at = datetime(2026, 4, 25, 11, 0, 0, tzinfo=UTC)

    with structlog.testing.capture_logs() as logs:
        result = await service.update_status(
            str(violation.id),
            status=SsrfViolationStatus.ALLOWED,
            resolved_by=NotBlankStr("op-1"),
            resolved_at=resolved_at,
        )

    assert result is True
    fetched = await repo.get(str(violation.id))
    assert fetched is not None
    assert fetched.status == SsrfViolationStatus.ALLOWED
    assert fetched.resolved_by == "op-1"
    assert fetched.resolved_at == resolved_at

    events = [log for log in logs if log["event"] == SECURITY_SSRF_VIOLATION_ALLOWED]
    assert len(events) == 1
    event = events[0]
    assert event["violation_id"] == str(violation.id)
    assert event["status"] == SsrfViolationStatus.ALLOWED.value
    assert event["resolved_by"] == "op-1"
    # Audit defines the WHO+WHEN contract; both fields must be on the
    # event payload so dashboards keyed on this event can reconstruct
    # the resolution timeline without joining tables.
    assert event["resolved_at"] == resolved_at.isoformat()


async def test_update_status_no_audit_when_row_missing() -> None:
    """Missing rows return ``False`` and emit no audit."""
    repo = _FakeSsrfViolationRepo()
    service = SsrfViolationService(repo=repo)
    resolved_at = datetime(2026, 4, 25, 11, 0, 0, tzinfo=UTC)

    with structlog.testing.capture_logs() as logs:
        result = await service.update_status(
            NotBlankStr("ghost"),
            status=SsrfViolationStatus.DENIED,
            resolved_by=NotBlankStr("op-1"),
            resolved_at=resolved_at,
        )

    assert result is False
    audits = [log for log in logs if log["event"] == SECURITY_SSRF_VIOLATION_DENIED]
    assert audits == []


async def test_update_status_rejects_pending_target() -> None:
    """Transitioning back to PENDING is invalid; one WARNING audit fires.

    The success-shape allow / deny event must NOT fire (no actual
    transition happened); a dedicated
    ``SECURITY_SSRF_VIOLATION_RESOLUTION_FAILED`` WARNING with
    ``error_type`` fires instead so SIEM dashboards keyed on the
    success verbs cannot misclassify a failed resolution as an
    actual decision.
    """
    repo = _FakeSsrfViolationRepo()
    service = SsrfViolationService(repo=repo)
    violation = _make_violation()
    await service.record(violation)
    resolved_at = datetime(2026, 4, 25, 11, 0, 0, tzinfo=UTC)

    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(ValueError, match="PENDING"),
    ):
        await service.update_status(
            str(violation.id),
            status=SsrfViolationStatus.PENDING,
            resolved_by=NotBlankStr("op-1"),
            resolved_at=resolved_at,
        )

    success_audits = [
        log
        for log in logs
        if log["event"]
        in {SECURITY_SSRF_VIOLATION_ALLOWED, SECURITY_SSRF_VIOLATION_DENIED}
    ]
    assert success_audits == [], (
        f"success-shape events must NOT fire on invalid transition "
        f"-- got {success_audits}"
    )
    failure_audits = [
        log for log in logs if log["event"] == SECURITY_SSRF_VIOLATION_RESOLUTION_FAILED
    ]
    warning_audits = [
        log for log in failure_audits if log.get("log_level") == "warning"
    ]
    assert len(warning_audits) == 1
    assert warning_audits[0]["error_type"] == "ValueError"
    assert warning_audits[0]["violation_id"] == str(violation.id)
    assert warning_audits[0]["status"] == SsrfViolationStatus.PENDING.value


async def test_update_status_no_audit_when_already_resolved() -> None:
    """Idempotent re-resolution returns ``False`` with no second audit."""
    repo = _FakeSsrfViolationRepo()
    service = SsrfViolationService(repo=repo)
    violation = _make_violation()
    await service.record(violation)
    resolved_at = datetime(2026, 4, 25, 11, 0, 0, tzinfo=UTC)

    await service.update_status(
        str(violation.id),
        status=SsrfViolationStatus.ALLOWED,
        resolved_by=NotBlankStr("op-1"),
        resolved_at=resolved_at,
    )

    with structlog.testing.capture_logs() as logs:
        result = await service.update_status(
            str(violation.id),
            status=SsrfViolationStatus.DENIED,
            resolved_by=NotBlankStr("op-2"),
            resolved_at=resolved_at,
        )

    assert result is False
    audits = [log for log in logs if log["event"] == SECURITY_SSRF_VIOLATION_DENIED]
    assert audits == []
