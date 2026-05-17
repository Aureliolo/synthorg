"""Conformance tests for ``PersonalityPresetRepository``."""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.persistence.preset_protocol import Preset, PresetFilterSpec
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration

_NOW_ISO = "2026-03-15T10:00:00+00:00"


class TestPersonalityPresetRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await backend.custom_presets.save(
            Preset(
                name=NotBlankStr("calm-analyst"),
                config_json='{"tone": "measured"}',
                description="A calm analyst",
                created_at=_NOW_ISO,
                updated_at=_NOW_ISO,
            )
        )

        preset = await backend.custom_presets.get(NotBlankStr("calm-analyst"))
        assert preset is not None
        assert preset.config_json == '{"tone": "measured"}'
        assert preset.description == "A calm analyst"
        assert preset.created_at == _NOW_ISO
        assert preset.updated_at == _NOW_ISO

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.custom_presets.get(NotBlankStr("ghost")) is None

    async def test_save_is_upsert(self, backend: PersistenceBackend) -> None:
        await backend.custom_presets.save(
            Preset(
                name=NotBlankStr("p1"),
                config_json='{"v": 1}',
                description="v1",
                created_at=_NOW_ISO,
                updated_at=_NOW_ISO,
            )
        )
        await backend.custom_presets.save(
            Preset(
                name=NotBlankStr("p1"),
                config_json='{"v": 2}',
                description="v2",
                created_at=_NOW_ISO,
                updated_at="2026-03-15T11:00:00+00:00",
            )
        )

        preset = await backend.custom_presets.get(NotBlankStr("p1"))
        assert preset is not None
        assert preset.config_json == '{"v": 2}'
        assert preset.description == "v2"
        assert preset.created_at == _NOW_ISO
        assert preset.updated_at == "2026-03-15T11:00:00+00:00"

    async def test_list_items(self, backend: PersistenceBackend) -> None:
        await backend.custom_presets.save(
            Preset(
                name=NotBlankStr("alpha"),
                config_json="{}",
                description="",
                created_at=_NOW_ISO,
                updated_at=_NOW_ISO,
            )
        )
        await backend.custom_presets.save(
            Preset(
                name=NotBlankStr("beta"),
                config_json="{}",
                description="",
                created_at=_NOW_ISO,
                updated_at=_NOW_ISO,
            )
        )

        presets = await backend.custom_presets.list_items()
        names = {p.name for p in presets}
        assert {"alpha", "beta"} <= names

    async def test_list_items_respects_limit(self, backend: PersistenceBackend) -> None:
        for i in range(5):
            await backend.custom_presets.save(
                Preset(
                    name=NotBlankStr(f"preset-{i:02d}"),
                    config_json="{}",
                    description="",
                    created_at=_NOW_ISO,
                    updated_at=_NOW_ISO,
                )
            )

        presets = await backend.custom_presets.list_items(limit=3)
        assert len(presets) == 3

    async def test_list_items_in_id_order(self, backend: PersistenceBackend) -> None:
        for name in ["z-last", "a-first", "m-middle"]:
            await backend.custom_presets.save(
                Preset(
                    name=NotBlankStr(name),
                    config_json="{}",
                    description="",
                    created_at=_NOW_ISO,
                    updated_at=_NOW_ISO,
                )
            )

        presets = await backend.custom_presets.list_items()
        names = [p.name for p in presets]
        assert names == sorted(names)

    async def test_count(self, backend: PersistenceBackend) -> None:
        assert await backend.custom_presets.count(PresetFilterSpec()) == 0

        await backend.custom_presets.save(
            Preset(
                name=NotBlankStr("c1"),
                config_json="{}",
                description="",
                created_at=_NOW_ISO,
                updated_at=_NOW_ISO,
            )
        )
        assert await backend.custom_presets.count(PresetFilterSpec()) == 1

    async def test_query_with_empty_spec(self, backend: PersistenceBackend) -> None:
        await backend.custom_presets.save(
            Preset(
                name=NotBlankStr("q1"),
                config_json="{}",
                description="",
                created_at=_NOW_ISO,
                updated_at=_NOW_ISO,
            )
        )

        presets = await backend.custom_presets.query(PresetFilterSpec())
        assert len(presets) == 1

    async def test_delete_existing(self, backend: PersistenceBackend) -> None:
        await backend.custom_presets.save(
            Preset(
                name=NotBlankStr("drop"),
                config_json="{}",
                description="",
                created_at=_NOW_ISO,
                updated_at=_NOW_ISO,
            )
        )

        deleted = await backend.custom_presets.delete(NotBlankStr("drop"))
        assert deleted is True
        assert await backend.custom_presets.get(NotBlankStr("drop")) is None

    async def test_delete_missing(self, backend: PersistenceBackend) -> None:
        assert await backend.custom_presets.delete(NotBlankStr("ghost")) is False
