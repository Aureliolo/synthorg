"""Unit tests for :class:`PersonalityService`."""

import pytest

from synthorg.api.dto_personalities import PresetSource
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.hr.personalities.service import PersonalityService
from synthorg.templates.preset_service import PresetEntry

pytestmark = pytest.mark.unit


def _entry(name: str, source: PresetSource = PresetSource.BUILTIN) -> PresetEntry:
    return PresetEntry(
        name=NotBlankStr(name),
        source=source,
        config={"description": f"{name} personality"},
        description=f"{name} personality",
    )


class _FakePresetService:
    """Minimal stand-in for :class:`PersonalityPresetService`."""

    def __init__(self, entries: list[PresetEntry]) -> None:
        self._entries = entries

    async def list_all(self) -> tuple[PresetEntry, ...]:
        return tuple(self._entries)

    async def get(self, name: str) -> PresetEntry:
        for entry in self._entries:
            if entry.name == name:
                return entry
        msg = f"Personality preset {name!r} not found"
        raise NotFoundError(msg)


class TestListPersonalities:
    """Happy path + pagination."""

    async def test_returns_all_with_total(self) -> None:
        entries = [_entry(n) for n in ("analytical", "creative", "direct")]
        service = PersonalityService(
            presets=_FakePresetService(entries),  # type: ignore[arg-type]
        )

        page, total = await service.list_personalities(offset=0, limit=50)

        assert total == 3
        assert [e.name for e in page] == ["analytical", "creative", "direct"]

    async def test_paginates(self) -> None:
        entries = [_entry(n) for n in ("a", "b", "c", "d", "e")]
        service = PersonalityService(
            presets=_FakePresetService(entries),  # type: ignore[arg-type]
        )

        page, total = await service.list_personalities(offset=2, limit=2)

        assert total == 5
        assert [e.name for e in page] == ["c", "d"]

    async def test_empty(self) -> None:
        service = PersonalityService(
            presets=_FakePresetService([]),  # type: ignore[arg-type]
        )

        page, total = await service.list_personalities(offset=0, limit=50)

        assert total == 0
        assert page == ()

    async def test_offset_past_end(self) -> None:
        entries = [_entry(n) for n in ("a", "b")]
        service = PersonalityService(
            presets=_FakePresetService(entries),  # type: ignore[arg-type]
        )

        page, total = await service.list_personalities(offset=10, limit=50)

        assert total == 2
        assert page == ()


class TestGetPersonality:
    """Present + missing."""

    async def test_returns_entry_when_present(self) -> None:
        entries = [_entry("analytical")]
        service = PersonalityService(
            presets=_FakePresetService(entries),  # type: ignore[arg-type]
        )

        result = await service.get_personality(NotBlankStr("analytical"))

        assert result is not None
        assert result.name == "analytical"

    async def test_returns_none_when_missing(self) -> None:
        service = PersonalityService(
            presets=_FakePresetService([]),  # type: ignore[arg-type]
        )

        result = await service.get_personality(NotBlankStr("unknown"))

        assert result is None
