"""Conformance tests for ``PersonalityPresetRepository``."""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration

_NOW_ISO = "2026-03-15T10:00:00+00:00"


class TestPersonalityPresetRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await backend.custom_presets.save(
            name=NotBlankStr("calm-analyst"),
            config_json='{"tone": "measured"}',
            description="A calm analyst",
            created_at=_NOW_ISO,
            updated_at=_NOW_ISO,
        )

        row = await backend.custom_presets.get(NotBlankStr("calm-analyst"))
        assert row is not None
        assert row.config_json == '{"tone": "measured"}'
        assert row.description == "A calm analyst"
        # Lock in the timestamp-normalisation contract: the Postgres impl
        # converts ``datetime`` columns back to ISO 8601 strings for
        # protocol parity, so the round-trip must preserve the exact
        # ISO string the caller provided.
        assert row.created_at == _NOW_ISO
        assert row.updated_at == _NOW_ISO

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.custom_presets.get(NotBlankStr("ghost")) is None

    async def test_save_is_upsert(self, backend: PersistenceBackend) -> None:
        await backend.custom_presets.save(
            name=NotBlankStr("p1"),
            config_json='{"v": 1}',
            description="v1",
            created_at=_NOW_ISO,
            updated_at=_NOW_ISO,
        )
        await backend.custom_presets.save(
            name=NotBlankStr("p1"),
            config_json='{"v": 2}',
            description="v2",
            created_at=_NOW_ISO,
            updated_at="2026-03-15T11:00:00+00:00",
        )

        row = await backend.custom_presets.get(NotBlankStr("p1"))
        assert row is not None
        assert row.config_json == '{"v": 2}'
        assert row.description == "v2"
        # Upsert preserves ``created_at`` but advances ``updated_at``.
        assert row.created_at == _NOW_ISO
        assert row.updated_at == "2026-03-15T11:00:00+00:00"

    async def test_list_all(self, backend: PersistenceBackend) -> None:
        await backend.custom_presets.save(
            name=NotBlankStr("alpha"),
            config_json="{}",
            description="",
            created_at=_NOW_ISO,
            updated_at=_NOW_ISO,
        )
        await backend.custom_presets.save(
            name=NotBlankStr("beta"),
            config_json="{}",
            description="",
            created_at=_NOW_ISO,
            updated_at=_NOW_ISO,
        )

        rows = await backend.custom_presets.list_all()
        names = {r.name for r in rows}
        assert {"alpha", "beta"} <= names

    async def test_list_all_respects_limit(self, backend: PersistenceBackend) -> None:
        for i in range(5):
            await backend.custom_presets.save(
                name=NotBlankStr(f"preset-{i:02d}"),
                config_json="{}",
                description="",
                created_at=_NOW_ISO,
                updated_at=_NOW_ISO,
            )

        rows = await backend.custom_presets.list_all(limit=3)
        assert len(rows) == 3

    async def test_count(self, backend: PersistenceBackend) -> None:
        assert await backend.custom_presets.count() == 0

        await backend.custom_presets.save(
            name=NotBlankStr("c1"),
            config_json="{}",
            description="",
            created_at=_NOW_ISO,
            updated_at=_NOW_ISO,
        )
        assert await backend.custom_presets.count() == 1

    async def test_delete_existing(self, backend: PersistenceBackend) -> None:
        await backend.custom_presets.save(
            name=NotBlankStr("drop"),
            config_json="{}",
            description="",
            created_at=_NOW_ISO,
            updated_at=_NOW_ISO,
        )

        deleted = await backend.custom_presets.delete(NotBlankStr("drop"))
        assert deleted is True
        assert await backend.custom_presets.get(NotBlankStr("drop")) is None

    async def test_delete_missing(self, backend: PersistenceBackend) -> None:
        assert await backend.custom_presets.delete(NotBlankStr("ghost")) is False
