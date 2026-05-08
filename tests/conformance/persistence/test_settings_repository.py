"""Parametrized conformance tests for SettingsRepository.

Runs identically against the SQLite and Postgres backends via the
shared ``backend`` fixture in ``conftest.py``.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend


def _ts(year: int, month: int, day: int, hour: int = 12) -> str:
    """Return an ISO 8601 string for a fixed UTC timestamp."""
    return datetime(year, month, day, hour, tzinfo=UTC).isoformat()


NS = NotBlankStr("test_ns")
NS_OTHER = NotBlankStr("other_ns")


@pytest.mark.integration
class TestSettingsGetSet:
    async def test_get_missing_returns_none(
        self,
        backend: PersistenceBackend,
    ) -> None:
        assert await backend.settings.get(NS, NotBlankStr("missing")) is None

    async def test_set_then_get_round_trip(
        self,
        backend: PersistenceBackend,
    ) -> None:
        assert (
            await backend.settings.set(NS, NotBlankStr("k1"), "v1", _ts(2026, 4, 10))
            is True
        )
        result = await backend.settings.get(NS, NotBlankStr("k1"))
        assert result is not None
        value, updated_at = result
        assert value == "v1"
        assert datetime.fromisoformat(updated_at) == datetime(
            2026, 4, 10, 12, tzinfo=UTC
        )

    async def test_set_upserts_existing_key(
        self,
        backend: PersistenceBackend,
    ) -> None:
        await backend.settings.set(NS, NotBlankStr("k2"), "initial", _ts(2026, 1, 1))
        await backend.settings.set(NS, NotBlankStr("k2"), "updated", _ts(2026, 2, 1))
        result = await backend.settings.get(NS, NotBlankStr("k2"))
        assert result is not None
        assert result[0] == "updated"


@pytest.mark.integration
class TestSettingsCompareAndSwap:
    async def test_cas_empty_string_inserts_new(
        self,
        backend: PersistenceBackend,
    ) -> None:
        ok = await backend.settings.set(
            NS,
            NotBlankStr("cas_new"),
            "first",
            _ts(2026, 1, 1),
            expected_updated_at="",
        )
        assert ok is True
        result = await backend.settings.get(NS, NotBlankStr("cas_new"))
        assert result is not None
        assert result[0] == "first"

    async def test_cas_empty_string_rejects_existing(
        self,
        backend: PersistenceBackend,
    ) -> None:
        await backend.settings.set(
            NS, NotBlankStr("cas_exist"), "first", _ts(2026, 1, 1)
        )
        ok = await backend.settings.set(
            NS,
            NotBlankStr("cas_exist"),
            "second",
            _ts(2026, 2, 1),
            expected_updated_at="",
        )
        assert ok is False
        result = await backend.settings.get(NS, NotBlankStr("cas_exist"))
        assert result is not None
        assert result[0] == "first"

    async def test_cas_matching_updates(
        self,
        backend: PersistenceBackend,
    ) -> None:
        await backend.settings.set(NS, NotBlankStr("cas_m"), "v1", _ts(2026, 1, 1))
        current = await backend.settings.get(NS, NotBlankStr("cas_m"))
        assert current is not None
        ok = await backend.settings.set(
            NS,
            NotBlankStr("cas_m"),
            "v2",
            _ts(2026, 2, 1),
            expected_updated_at=current[1],
        )
        assert ok is True

    async def test_cas_mismatch_rejects(
        self,
        backend: PersistenceBackend,
    ) -> None:
        await backend.settings.set(NS, NotBlankStr("cas_mm"), "v1", _ts(2026, 1, 1))
        ok = await backend.settings.set(
            NS,
            NotBlankStr("cas_mm"),
            "v2",
            _ts(2026, 2, 1),
            expected_updated_at=_ts(2020, 1, 1),
        )
        assert ok is False
        result = await backend.settings.get(NS, NotBlankStr("cas_mm"))
        assert result is not None
        assert result[0] == "v1"


@pytest.mark.integration
class TestSettingsListAndDelete:
    async def test_get_namespace_returns_sorted_by_key(
        self,
        backend: PersistenceBackend,
    ) -> None:
        await backend.settings.set(NS, NotBlankStr("b_key"), "b_val", _ts(2026, 1, 1))
        await backend.settings.set(NS, NotBlankStr("a_key"), "a_val", _ts(2026, 1, 1))
        await backend.settings.set(
            NS_OTHER, NotBlankStr("x_key"), "x_val", _ts(2026, 1, 1)
        )

        result = await backend.settings.get_namespace(NS)
        assert len(result) == 2
        assert result[0][0] == "a_key"
        assert result[1][0] == "b_key"

    async def test_get_all_returns_all_namespaces(
        self,
        backend: PersistenceBackend,
    ) -> None:
        await backend.settings.set(NS, NotBlankStr("k"), "v", _ts(2026, 1, 1))
        await backend.settings.set(NS_OTHER, NotBlankStr("k"), "v", _ts(2026, 1, 1))
        result = await backend.settings.get_all()
        namespaces = {row[0] for row in result}
        assert "test_ns" in namespaces
        assert "other_ns" in namespaces

    async def test_delete_returns_true_when_present(
        self,
        backend: PersistenceBackend,
    ) -> None:
        await backend.settings.set(NS, NotBlankStr("k"), "v", _ts(2026, 1, 1))
        assert await backend.settings.delete(NS, NotBlankStr("k")) is True
        assert await backend.settings.get(NS, NotBlankStr("k")) is None

    async def test_delete_returns_false_when_missing(
        self,
        backend: PersistenceBackend,
    ) -> None:
        assert await backend.settings.delete(NS, NotBlankStr("missing")) is False

    async def test_delete_namespace_removes_all_keys(
        self,
        backend: PersistenceBackend,
    ) -> None:
        await backend.settings.set(NS, NotBlankStr("k1"), "v1", _ts(2026, 1, 1))
        await backend.settings.set(NS, NotBlankStr("k2"), "v2", _ts(2026, 1, 1))
        await backend.settings.set(NS_OTHER, NotBlankStr("k"), "v", _ts(2026, 1, 1))

        count = await backend.settings.delete_namespace(NS)
        assert count == 2
        assert await backend.settings.get_namespace(NS) == ()
        # Other namespace untouched
        other = await backend.settings.get_namespace(NS_OTHER)
        assert len(other) == 1

    async def test_delete_namespace_returning_keys_is_atomic(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """``delete_namespace_returning_keys`` returns exactly the deleted keys.

        ``SettingsService.delete_namespace`` uses this method to
        scope per-key change-publish notifications to rows that
        genuinely changed.  A drift between the returned keys and the
        deleted rows would either drop a publish or fire a phantom
        one.
        """
        await backend.settings.set(NS, NotBlankStr("k1"), "v1", _ts(2026, 1, 1))
        await backend.settings.set(NS, NotBlankStr("k2"), "v2", _ts(2026, 1, 1))
        await backend.settings.set(NS_OTHER, NotBlankStr("k"), "v", _ts(2026, 1, 1))

        removed = await backend.settings.delete_namespace_returning_keys(NS)
        assert sorted(str(k) for k in removed) == ["k1", "k2"]
        # Rows under NS are gone, NS_OTHER is untouched.
        assert await backend.settings.get_namespace(NS) == ()
        other = await backend.settings.get_namespace(NS_OTHER)
        assert len(other) == 1

    async def test_delete_namespace_returning_keys_empty(
        self,
        backend: PersistenceBackend,
    ) -> None:
        """An empty namespace returns an empty tuple, never raises."""
        removed = await backend.settings.delete_namespace_returning_keys(NS)
        assert removed == ()
