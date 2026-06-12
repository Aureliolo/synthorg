"""Unit tests for :class:`PersonalityService`."""

from unittest.mock import AsyncMock

import pytest

from synthorg.api.dto_personalities import PresetSource
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.hr.personalities.service import PersonalityService
from synthorg.templates.preset_service import PersonalityPresetService, PresetEntry
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _entry(name: str, source: PresetSource = PresetSource.BUILTIN) -> PresetEntry:
    return PresetEntry(
        name=NotBlankStr(name),
        source=source,
        config={"description": f"{name} personality"},
        description=f"{name} personality",
    )


def _preset_service(entries: list[PresetEntry]) -> PersonalityPresetService:
    """Autospec ``PersonalityPresetService`` backed by *entries*."""

    async def _get(name: str) -> PresetEntry:
        for entry in entries:
            if entry.name == name:
                return entry
        msg = f"Personality preset {name!r} not found"
        raise NotFoundError(msg)

    service: PersonalityPresetService = mock_of[PersonalityPresetService](
        list_all=AsyncMock(return_value=tuple(entries)),
        get=AsyncMock(side_effect=_get),
    )
    return service


class TestListPersonalities:
    """Happy path + pagination."""

    async def test_returns_all_with_total(self) -> None:
        entries = [_entry(n) for n in ("analytical", "creative", "direct")]
        service = PersonalityService(
            presets=_preset_service(entries),
        )

        page, total = await service.list_personalities(offset=0, limit=50)

        assert total == 3
        assert [e.name for e in page] == ["analytical", "creative", "direct"]

    async def test_paginates(self) -> None:
        entries = [_entry(n) for n in ("a", "b", "c", "d", "e")]
        service = PersonalityService(
            presets=_preset_service(entries),
        )

        page, total = await service.list_personalities(offset=2, limit=2)

        assert total == 5
        assert [e.name for e in page] == ["c", "d"]

    async def test_empty(self) -> None:
        service = PersonalityService(
            presets=_preset_service([]),
        )

        page, total = await service.list_personalities(offset=0, limit=50)

        assert total == 0
        assert page == ()

    async def test_offset_past_end(self) -> None:
        entries = [_entry(n) for n in ("a", "b")]
        service = PersonalityService(
            presets=_preset_service(entries),
        )

        page, total = await service.list_personalities(offset=10, limit=50)

        assert total == 2
        assert page == ()


class TestGetPersonality:
    """Present + missing."""

    async def test_returns_entry_when_present(self) -> None:
        entries = [_entry("analytical")]
        service = PersonalityService(
            presets=_preset_service(entries),
        )

        result = await service.get_personality(NotBlankStr("analytical"))

        assert result is not None
        assert result.name == "analytical"

    async def test_returns_none_when_missing(self) -> None:
        service = PersonalityService(
            presets=_preset_service([]),
        )

        result = await service.get_personality(NotBlankStr("unknown"))

        assert result is None
