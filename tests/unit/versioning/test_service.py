"""Tests for VersioningService."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ConfigDict

from synthorg.versioning.hashing import compute_content_hash
from synthorg.versioning.models import VersionSnapshot
from synthorg.versioning.service import VersioningService

_NOW = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)


class _Simple(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    value: int


def _make_snapshot(
    entity_id: str = "ent-001",
    version: int = 1,
    model: _Simple | None = None,
) -> VersionSnapshot[_Simple]:
    if model is None:
        model = _Simple(name="test", value=1)
    return VersionSnapshot(
        entity_id=entity_id,
        version=version,
        content_hash=compute_content_hash(model),
        snapshot=model,
        saved_by="system",
        saved_at=_NOW,
    )


def _make_repo(
    latest: VersionSnapshot[_Simple] | None = None,
) -> AsyncMock:
    """Build a mock VersionRepository."""
    repo = AsyncMock()
    repo.get_latest_version.return_value = latest
    repo.save_version.return_value = True
    return repo


class TestSnapshotIfChanged:
    """Content-aware snapshot creation."""

    @pytest.mark.unit
    async def test_creates_version_when_no_prior(self) -> None:
        repo = _make_repo(latest=None)
        svc = VersioningService(repo)
        model = _Simple(name="first", value=1)

        result = await svc.snapshot_if_changed("ent-001", model, "user")

        assert result is not None
        assert result.version == 1
        assert result.entity_id == "ent-001"
        assert result.saved_by == "user"
        assert result.content_hash == compute_content_hash(model)
        repo.save_version.assert_called_once()

    @pytest.mark.unit
    async def test_increments_version_from_latest(self) -> None:
        model = _Simple(name="old", value=1)
        existing = _make_snapshot(version=3, model=model)
        repo = _make_repo(latest=existing)
        svc = VersioningService(repo)
        new_model = _Simple(name="new", value=2)

        result = await svc.snapshot_if_changed("ent-001", new_model, "user")

        assert result is not None
        assert result.version == 4

    @pytest.mark.unit
    async def test_skips_when_content_unchanged(self) -> None:
        model = _Simple(name="same", value=42)
        existing = _make_snapshot(version=2, model=model)
        repo = _make_repo(latest=existing)
        svc = VersioningService(repo)

        result = await svc.snapshot_if_changed("ent-001", model, "user")

        assert result is None
        repo.save_version.assert_not_called()

    @pytest.mark.unit
    async def test_saves_when_single_field_differs(self) -> None:
        old = _Simple(name="same", value=42)
        existing = _make_snapshot(version=1, model=old)
        repo = _make_repo(latest=existing)
        svc = VersioningService(repo)
        new = _Simple(name="same", value=43)

        result = await svc.snapshot_if_changed("ent-001", new, "user")

        assert result is not None
        repo.save_version.assert_called_once()

    @pytest.mark.unit
    async def test_snapshot_embeds_correct_model(self) -> None:
        repo = _make_repo(latest=None)
        svc = VersioningService(repo)
        model = _Simple(name="embed", value=7)

        result = await svc.snapshot_if_changed("ent-002", model, "sys")

        assert result is not None
        assert result.snapshot == model


class TestGetLatest:
    """get_latest delegates to repository."""

    @pytest.mark.unit
    async def test_returns_none_when_no_versions(self) -> None:
        repo = _make_repo(latest=None)
        svc = VersioningService(repo)
        assert await svc.get_latest("ent-001") is None

    @pytest.mark.unit
    async def test_returns_latest_from_repo(self) -> None:
        snap = _make_snapshot(version=5)
        repo = _make_repo(latest=snap)
        svc = VersioningService(repo)
        result = await svc.get_latest("ent-001")
        assert result is not None
        assert result.version == 5


class TestConcurrentSave:
    """Handles INSERT OR IGNORE concurrent duplicate detection."""

    @pytest.mark.unit
    async def test_returns_existing_when_insert_ignored_same_content(self) -> None:
        # save_version returns False: concurrent writer already inserted
        # the same content. _resolve_conflict re-fetches and finds a
        # matching content_hash, so it returns the persisted row.
        model = _Simple(name="first", value=1)
        existing = _make_snapshot(version=1, model=model)
        repo = AsyncMock()
        # First call: no prior version. Second call (from _resolve_conflict):
        # returns the winner's snapshot (same content hash).
        repo.get_latest_version.side_effect = [None, existing]
        repo.save_version.return_value = False
        svc = VersioningService(repo)

        result = await svc.snapshot_if_changed("ent-001", model, "user")

        repo.save_version.assert_called_once()
        assert repo.get_latest_version.call_count == 2
        assert result is existing

    @pytest.mark.unit
    async def test_retries_when_insert_ignored_different_content(self) -> None:
        # save_version returns False then True: concurrent writer had
        # different content, so _resolve_conflict retries with next version.
        model = _Simple(name="ours", value=42)
        other = _Simple(name="theirs", value=99)
        other_snap = _make_snapshot(version=1, model=other)
        repo = AsyncMock()
        # First call: no prior. Second call (resolve): returns other's snap.
        repo.get_latest_version.side_effect = [None, other_snap]
        repo.save_version.side_effect = [False, True]
        svc = VersioningService(repo)

        result = await svc.snapshot_if_changed("ent-001", model, "user")

        assert repo.save_version.call_count == 2
        assert result is not None
        assert result.version == 2  # retried with other.version + 1


class TestSnapshotDeterminism:
    """Property: same content always produces the same hash."""

    @pytest.mark.unit
    @given(
        name=st.text(min_size=1, max_size=50),
        value=st.integers(min_value=0, max_value=10_000),
    )
    def test_same_model_same_hash(self, name: str, value: int) -> None:
        model = _Simple(name=name, value=value)
        h1 = compute_content_hash(model)
        h2 = compute_content_hash(model)
        assert h1 == h2

    @pytest.mark.unit
    @given(
        name_a=st.text(min_size=1, max_size=50),
        value_a=st.integers(min_value=0, max_value=10_000),
        name_b=st.text(min_size=1, max_size=50),
        value_b=st.integers(min_value=0, max_value=10_000),
    )
    def test_different_models_different_hash(
        self, name_a: str, value_a: int, name_b: str, value_b: int
    ) -> None:
        from hypothesis import assume

        model_a = _Simple(name=name_a, value=value_a)
        model_b = _Simple(name=name_b, value=value_b)
        assume(model_a != model_b)
        assert compute_content_hash(model_a) != compute_content_hash(model_b)
