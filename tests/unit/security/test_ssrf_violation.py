"""Tests for SsrfViolation model."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from synthorg.security.ssrf_violation import SsrfViolation, SsrfViolationStatus
from tests._shared import as_uuid

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)


class TestSsrfViolationStatus:
    """Tests for the SsrfViolationStatus enum."""

    def test_values(self) -> None:
        assert SsrfViolationStatus.PENDING.value == "pending"
        assert SsrfViolationStatus.ALLOWED.value == "allowed"
        assert SsrfViolationStatus.DENIED.value == "denied"


class TestSsrfViolationModel:
    """Tests for the SsrfViolation Pydantic model."""

    def test_pending_violation(self) -> None:
        v = SsrfViolation(
            id=as_uuid("v-1"),
            timestamp=_NOW,
            url="http://host.docker.internal:11434",
            hostname="host.docker.internal",
            port=11434,
            resolved_ip="192.168.65.254",
            blocked_range="192.168.0.0/16",
            provider_name="test-provider",
        )
        assert v.status == SsrfViolationStatus.PENDING
        assert v.resolved_by is None
        assert v.resolved_at is None

    def test_allowed_violation(self) -> None:
        v = SsrfViolation(
            id=as_uuid("v-2"),
            timestamp=_NOW,
            url="http://host.docker.internal:11434",
            hostname="host.docker.internal",
            port=11434,
            status=SsrfViolationStatus.ALLOWED,
            resolved_by="user-1",
            resolved_at=_NOW + timedelta(minutes=5),
        )
        assert v.status == SsrfViolationStatus.ALLOWED
        assert v.resolved_by == "user-1"
        assert v.resolved_at == _NOW + timedelta(minutes=5)

    def test_denied_violation(self) -> None:
        v = SsrfViolation(
            id=as_uuid("v-3"),
            timestamp=_NOW,
            url="http://host.docker.internal:11434",
            hostname="host.docker.internal",
            port=11434,
            status=SsrfViolationStatus.DENIED,
            resolved_by="admin-1",
            resolved_at=_NOW + timedelta(minutes=5),
        )
        assert v.status == SsrfViolationStatus.DENIED
        assert v.resolved_by == "admin-1"
        assert v.resolved_at == _NOW + timedelta(minutes=5)

    def test_frozen(self) -> None:
        v = SsrfViolation(
            id=as_uuid("v-1"),
            timestamp=_NOW,
            url="http://host.docker.internal:11434",
            hostname="host.docker.internal",
            port=11434,
        )
        with pytest.raises(ValidationError):
            v.status = SsrfViolationStatus.ALLOWED  # type: ignore[misc]

    def test_pending_with_resolved_by_rejected(self) -> None:
        with pytest.raises(ValidationError, match="resolved_by"):
            SsrfViolation(
                id=as_uuid("v-bad"),
                timestamp=_NOW,
                url="http://example.com:80",
                hostname="example.com",
                port=80,
                status=SsrfViolationStatus.PENDING,
                resolved_by="user-1",
            )

    def test_pending_with_resolved_at_rejected(self) -> None:
        with pytest.raises(ValidationError, match="resolved_at"):
            SsrfViolation(
                id=as_uuid("v-bad"),
                timestamp=_NOW,
                url="http://example.com:80",
                hostname="example.com",
                port=80,
                status=SsrfViolationStatus.PENDING,
                resolved_at=_NOW + timedelta(minutes=5),
            )

    def test_allowed_without_resolved_by_rejected(self) -> None:
        with pytest.raises(ValidationError, match="resolved_by"):
            SsrfViolation(
                id=as_uuid("v-bad"),
                timestamp=_NOW,
                url="http://example.com:80",
                hostname="example.com",
                port=80,
                status=SsrfViolationStatus.ALLOWED,
            )

    def test_denied_without_resolved_by_rejected(self) -> None:
        with pytest.raises(ValidationError, match="resolved_by"):
            SsrfViolation(
                id=as_uuid("v-bad"),
                timestamp=_NOW,
                url="http://example.com:80",
                hostname="example.com",
                port=80,
                status=SsrfViolationStatus.DENIED,
            )

    def test_port_bounds(self) -> None:
        with pytest.raises(ValidationError, match="port"):
            SsrfViolation(
                id=as_uuid("v-1"),
                timestamp=_NOW,
                url="http://example.com:0",
                hostname="example.com",
                port=0,
            )

    def test_port_max(self) -> None:
        v = SsrfViolation(
            id=as_uuid("v-1"),
            timestamp=_NOW,
            url="http://example.com:65535",
            hostname="example.com",
            port=65535,
        )
        assert v.port == 65535

    def test_optional_fields_none(self) -> None:
        v = SsrfViolation(
            id=as_uuid("v-1"),
            timestamp=_NOW,
            url="http://example.com:80",
            hostname="example.com",
            port=80,
        )
        assert v.resolved_ip is None
        assert v.blocked_range is None
        assert v.provider_name is None

    def test_serialization_roundtrip(self) -> None:
        v = SsrfViolation(
            id=as_uuid("v-1"),
            timestamp=_NOW,
            url="http://host.docker.internal:11434",
            hostname="host.docker.internal",
            port=11434,
            resolved_ip="192.168.65.254",
            blocked_range="192.168.0.0/16",
            provider_name="test-provider",
        )
        data = v.model_dump()
        restored = SsrfViolation.model_validate(data)
        assert restored == v
