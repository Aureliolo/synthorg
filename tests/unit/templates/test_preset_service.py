"""Tests for PersonalityPresetService."""

import json
from typing import Any

import pytest
from pydantic import JsonValue

from synthorg.core.domain_errors import ConflictError, NotFoundError, ValidationError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.preset_protocol import Preset
from synthorg.templates.preset_service import PersonalityPresetService
from synthorg.templates.presets import PERSONALITY_PRESETS
from tests.unit.api.fakes import FakePersonalityPresetRepository


def _make_valid_config() -> dict[str, JsonValue]:
    """Return a valid PersonalityConfig dict for testing."""
    return {
        "traits": ["friendly", "curious"],
        "communication_style": "warm",
        "risk_tolerance": "medium",
        "creativity": "high",
        "description": "A test preset",
        "openness": 0.8,
        "conscientiousness": 0.6,
        "extraversion": 0.7,
        "agreeableness": 0.9,
        "stress_response": 0.5,
        "decision_making": "consultative",
        "collaboration": "team",
        "verbosity": "balanced",
        "conflict_approach": "collaborate",
    }


@pytest.fixture
def repo() -> FakePersonalityPresetRepository:
    return FakePersonalityPresetRepository()


@pytest.fixture
def service(
    repo: FakePersonalityPresetRepository,
) -> PersonalityPresetService:
    return PersonalityPresetService(repository=repo)


@pytest.mark.unit
class TestListAll:
    async def test_lists_all_builtins(self, service: PersonalityPresetService) -> None:
        entries = await service.list_all()
        builtin_names = {e.name for e in entries if e.source == "builtin"}
        assert builtin_names == set(PERSONALITY_PRESETS.keys())

    async def test_includes_custom_presets(
        self,
        service: PersonalityPresetService,
        repo: FakePersonalityPresetRepository,
    ) -> None:
        config = _make_valid_config()
        config_json = json.dumps(config, sort_keys=True)
        await repo.save(
            Preset(
                name=NotBlankStr("my_custom"),
                config_json=config_json,
                description="Custom",
                created_at="2026-03-31T00:00:00+00:00",
                updated_at="2026-03-31T00:00:00+00:00",
            )
        )
        entries = await service.list_all()
        custom = [e for e in entries if e.source == "custom"]
        assert len(custom) == 1
        assert custom[0].name == "my_custom"

    async def test_sorted_by_name(self, service: PersonalityPresetService) -> None:
        entries = await service.list_all()
        names = [e.name for e in entries]
        assert names == sorted(names)

    async def test_source_tags_correct(self, service: PersonalityPresetService) -> None:
        entries = await service.list_all()
        for entry in entries:
            if entry.name in PERSONALITY_PRESETS:
                assert entry.source == "builtin"


@pytest.mark.unit
class TestGet:
    async def test_get_builtin(self, service: PersonalityPresetService) -> None:
        entry = await service.get("visionary_leader")
        assert entry.source == "builtin"
        assert entry.name == "visionary_leader"
        traits = entry.config.get("traits", ())
        assert isinstance(traits, list)
        assert "strategic" in traits

    async def test_get_builtin_case_insensitive(
        self, service: PersonalityPresetService
    ) -> None:
        entry = await service.get("Visionary_Leader")
        assert entry.name == "visionary_leader"

    async def test_get_custom(
        self,
        service: PersonalityPresetService,
        repo: FakePersonalityPresetRepository,
    ) -> None:
        config = _make_valid_config()
        config_json = json.dumps(config, sort_keys=True)
        await repo.save(
            Preset(
                name=NotBlankStr("my_custom"),
                config_json=config_json,
                description="Custom",
                created_at="2026-03-31T00:00:00+00:00",
                updated_at="2026-03-31T00:00:00+00:00",
            )
        )
        entry = await service.get("my_custom")
        assert entry.source == "custom"
        assert entry.created_at == "2026-03-31T00:00:00+00:00"

    async def test_get_not_found(self, service: PersonalityPresetService) -> None:
        with pytest.raises(NotFoundError):
            await service.get("nonexistent_preset")

    async def test_get_blank_name_raises_not_found(
        self, service: PersonalityPresetService
    ) -> None:
        with pytest.raises(NotFoundError):
            await service.get("  ")


@pytest.mark.unit
class TestCreate:
    async def test_create_valid(self, service: PersonalityPresetService) -> None:
        config = _make_valid_config()
        entry = await service.create("my_custom", config)
        assert entry.name == "my_custom"
        assert entry.source == "custom"
        assert entry.created_at is not None
        assert entry.updated_at is not None

    async def test_create_normalizes_name(
        self, service: PersonalityPresetService
    ) -> None:
        config = _make_valid_config()
        entry = await service.create("  My_Custom  ", config)
        assert entry.name == "my_custom"

    async def test_create_rejects_builtin_shadow(
        self, service: PersonalityPresetService
    ) -> None:
        config = _make_valid_config()
        with pytest.raises(ConflictError, match="builtin"):
            await service.create("visionary_leader", config)

    async def test_create_rejects_duplicate_custom(
        self, service: PersonalityPresetService
    ) -> None:
        config = _make_valid_config()
        await service.create("unique_preset", config)
        with pytest.raises(ConflictError, match="already exists"):
            await service.create("unique_preset", config)

    async def test_create_rejects_invalid_config(
        self, service: PersonalityPresetService
    ) -> None:
        config = _make_valid_config()
        config["openness"] = 2.0  # Out of range
        with pytest.raises(ValidationError):
            await service.create("bad_preset", config)

    async def test_create_rejects_invalid_name_format(
        self, service: PersonalityPresetService
    ) -> None:
        config = _make_valid_config()
        with pytest.raises(ValidationError, match="Invalid preset name"):
            await service.create("has spaces", config)

    async def test_create_rejects_empty_name(
        self, service: PersonalityPresetService
    ) -> None:
        config = _make_valid_config()
        with pytest.raises(ValidationError, match="blank"):
            await service.create("  ", config)


@pytest.mark.unit
class TestUpdate:
    async def test_update_existing_custom(
        self, service: PersonalityPresetService
    ) -> None:
        config = _make_valid_config()
        await service.create("my_preset", config)
        config["openness"] = 0.9
        entry = await service.update("my_preset", config)
        assert entry.config["openness"] == 0.9
        assert entry.source == "custom"

    async def test_update_preserves_created_at(
        self, service: PersonalityPresetService
    ) -> None:
        config = _make_valid_config()
        created = await service.create("my_preset", config)
        config["openness"] = 0.1
        updated = await service.update("my_preset", config)
        assert updated.created_at == created.created_at
        assert updated.updated_at != created.updated_at

    async def test_update_rejects_builtin(
        self, service: PersonalityPresetService
    ) -> None:
        config = _make_valid_config()
        with pytest.raises(ConflictError, match="builtin"):
            await service.update("visionary_leader", config)

    async def test_update_not_found(self, service: PersonalityPresetService) -> None:
        config = _make_valid_config()
        with pytest.raises(NotFoundError):
            await service.update("nonexistent", config)

    async def test_update_rejects_invalid_config(
        self, service: PersonalityPresetService
    ) -> None:
        config = _make_valid_config()
        await service.create("update_invalid", config)
        config["openness"] = 2.0
        with pytest.raises(ValidationError):
            await service.update("update_invalid", config)


@pytest.mark.unit
class TestDelete:
    async def test_delete_existing_custom(
        self, service: PersonalityPresetService
    ) -> None:
        config = _make_valid_config()
        await service.create("my_preset", config)
        await service.delete("my_preset")
        with pytest.raises(NotFoundError):
            await service.get("my_preset")

    async def test_delete_rejects_builtin(
        self, service: PersonalityPresetService
    ) -> None:
        with pytest.raises(ConflictError, match="builtin"):
            await service.delete("visionary_leader")

    async def test_delete_not_found(self, service: PersonalityPresetService) -> None:
        with pytest.raises(NotFoundError):
            await service.delete("nonexistent")


@pytest.mark.unit
class TestGetSchema:
    def test_returns_json_schema(self) -> None:
        schema: Any = PersonalityPresetService.get_schema()
        assert "properties" in schema
        assert "openness" in schema["properties"]
        assert "traits" in schema["properties"]
        assert schema["properties"]["openness"]["type"] == "number"


@pytest.mark.unit
class TestFetchCustomPresetsMap:
    async def test_empty_repo_returns_empty_dict(
        self,
        repo: FakePersonalityPresetRepository,
    ) -> None:
        from synthorg.templates.preset_service import fetch_custom_presets_map

        result = await fetch_custom_presets_map(repo)
        assert result == {}

    async def test_returns_name_to_config_mapping(
        self,
        repo: FakePersonalityPresetRepository,
    ) -> None:
        from synthorg.templates.preset_service import fetch_custom_presets_map

        config = _make_valid_config()
        await repo.save(
            Preset(
                name="test_preset",
                config_json=json.dumps(config),
                description="test",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )
        result = await fetch_custom_presets_map(repo)
        assert "test_preset" in result
        assert result["test_preset"]["communication_style"] == "warm"

    async def test_multiple_presets(
        self,
        repo: FakePersonalityPresetRepository,
    ) -> None:
        from synthorg.templates.preset_service import fetch_custom_presets_map

        config = _make_valid_config()
        for name in ("preset_a", "preset_b"):
            await repo.save(
                Preset(
                    name=NotBlankStr(name),
                    config_json=json.dumps(config),
                    description="test",
                    created_at="2026-01-01T00:00:00+00:00",
                    updated_at="2026-01-01T00:00:00+00:00",
                )
            )
        result = await fetch_custom_presets_map(repo)
        assert len(result) == 2
        assert "preset_a" in result
        assert "preset_b" in result

    async def test_corrupt_json_skipped(
        self,
        repo: FakePersonalityPresetRepository,
    ) -> None:
        """Corrupt JSON row is skipped, other presets still returned."""
        from synthorg.templates.preset_service import fetch_custom_presets_map

        config = _make_valid_config()
        await repo.save(
            Preset(
                name="good_preset",
                config_json=json.dumps(config),
                description="good",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )
        await repo.save(
            Preset(
                name="corrupt_preset",
                config_json="{not valid json",
                description="corrupt",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )
        result = await fetch_custom_presets_map(repo)
        assert "good_preset" in result
        assert "corrupt_preset" not in result

    async def test_keys_are_lowercased(
        self,
        repo: FakePersonalityPresetRepository,
    ) -> None:
        """Returned keys are lowercased regardless of stored case."""
        from synthorg.templates.preset_service import fetch_custom_presets_map

        config = _make_valid_config()
        await repo.save(
            Preset(
                name="My_Preset",
                config_json=json.dumps(config),
                description="test",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )
        result = await fetch_custom_presets_map(repo)
        assert "my_preset" in result
