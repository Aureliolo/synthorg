"""Conformance tests for ``RiskOverrideRepository`` (SQLite + Postgres).

Covers save/get/list_active/revoke across both backends.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.security.rules.risk_override import RiskTierOverride

pytestmark = pytest.mark.integration

# Use wall-clock now so ``list_active`` (which filters by ``expires_at > now``)
# returns rows instead of treating every fixture as already-expired.
_NOW = datetime.now(UTC)
_EXPIRES = _NOW + timedelta(hours=24)
_PAST = _NOW - timedelta(hours=1)


def _override(  # noqa: PLR0913
    *,
    override_id: str = "ovr-001",
    action_type: str = "spend:approve",
    original: ApprovalRiskLevel = ApprovalRiskLevel.HIGH,
    override: ApprovalRiskLevel = ApprovalRiskLevel.MEDIUM,
    created_at: datetime = _NOW,
    expires_at: datetime = _EXPIRES,
) -> RiskTierOverride:
    return RiskTierOverride(
        id=NotBlankStr(override_id),
        action_type=NotBlankStr(action_type),
        original_tier=original,
        override_tier=override,
        reason=NotBlankStr("sprint compliance window"),
        created_by=NotBlankStr("alice"),
        created_at=created_at,
        expires_at=expires_at,
    )


class TestRiskOverrideRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        ov = _override()
        await backend.risk_overrides.save(ov)

        fetched = await backend.risk_overrides.get(NotBlankStr("ovr-001"))
        assert fetched is not None
        assert fetched.action_type == "spend:approve"
        assert fetched.original_tier == ApprovalRiskLevel.HIGH
        assert fetched.override_tier == ApprovalRiskLevel.MEDIUM
        assert fetched.revoked_at is None

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.risk_overrides.get(NotBlankStr("ghost")) is None

    async def test_list_active_excludes_revoked(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.risk_overrides.save(_override(override_id="keep"))
        await backend.risk_overrides.save(_override(override_id="drop"))

        revoked = await backend.risk_overrides.revoke(
            NotBlankStr("drop"),
            revoked_by=NotBlankStr("bob"),
            revoked_at=_NOW + timedelta(minutes=5),
        )
        assert revoked is True

        active = await backend.risk_overrides.list_active()
        ids = {o.id for o in active}
        assert "keep" in ids
        assert "drop" not in ids

    async def test_list_active_excludes_expired(
        self, backend: PersistenceBackend
    ) -> None:
        # Past-expiry override (constructed with valid created_at < expires_at
        # timeline but both in the past relative to "now").
        fresh_created = _PAST - timedelta(hours=2)
        await backend.risk_overrides.save(
            _override(
                override_id="expired",
                created_at=fresh_created,
                expires_at=_PAST,
            ),
        )
        await backend.risk_overrides.save(_override(override_id="current"))

        active = await backend.risk_overrides.list_active()
        ids = {o.id for o in active}
        assert "current" in ids
        assert "expired" not in ids

    async def test_list_active_respects_limit(
        self, backend: PersistenceBackend
    ) -> None:
        for i in range(5):
            await backend.risk_overrides.save(_override(override_id=f"ovr-lim-{i}"))

        rows = await backend.risk_overrides.list_active(limit=3)
        assert len(rows) == 3

    async def test_revoke_missing_returns_false(
        self, backend: PersistenceBackend
    ) -> None:
        result = await backend.risk_overrides.revoke(
            NotBlankStr("nonexistent"),
            revoked_by=NotBlankStr("bob"),
            revoked_at=_NOW,
        )
        assert result is False

    async def test_revoke_records_metadata(self, backend: PersistenceBackend) -> None:
        await backend.risk_overrides.save(_override(override_id="meta"))

        await backend.risk_overrides.revoke(
            NotBlankStr("meta"),
            revoked_by=NotBlankStr("alice"),
            revoked_at=_NOW + timedelta(minutes=30),
        )
        fetched = await backend.risk_overrides.get(NotBlankStr("meta"))
        assert fetched is not None
        assert fetched.revoked_by == "alice"
        assert fetched.revoked_at is not None
