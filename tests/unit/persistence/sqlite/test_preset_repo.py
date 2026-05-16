"""Tests for SQLitePersonalityPresetRepository."""

import json

import aiosqlite
import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.preset_protocol import Preset, PresetFilterSpec
from synthorg.persistence.sqlite.preset_repo import (
    SQLitePersonalityPresetRepository,
)
from tests._shared.persistence import make_private_write_context


@pytest.fixture
def repo(
    migrated_db: aiosqlite.Connection,
) -> SQLitePersonalityPresetRepository:
    return SQLitePersonalityPresetRepository(
        migrated_db, write_context=make_private_write_context()
    )


def _sample_config_json() -> str:
    return json.dumps(
        {
            "traits": ["friendly", "curious"],
            "communication_style": "warm",
            "risk_tolerance": "medium",
            "creativity": "high",
            "description": "A friendly preset",
            "openness": 0.8,
            "conscientiousness": 0.6,
            "extraversion": 0.7,
            "agreeableness": 0.9,
            "stress_response": 0.5,
            "decision_making": "consultative",
            "collaboration": "team",
            "verbosity": "balanced",
            "conflict_approach": "collaborate",
        },
        sort_keys=True,
    )


@pytest.mark.unit
class TestSQLitePersonalityPresetRepository:
    async def test_save_and_get(self, repo: SQLitePersonalityPresetRepository) -> None:
        config_json = _sample_config_json()
        await repo.save(
            Preset(
                name=NotBlankStr("my_preset"),
                config_json=config_json,
                description="A friendly preset",
                created_at="2026-03-31T00:00:00+00:00",
                updated_at="2026-03-31T00:00:00+00:00",
            )
        )
        result = await repo.get(NotBlankStr("my_preset"))
        assert result is not None
        assert json.loads(result.config_json) == json.loads(config_json)
        assert result.description == "A friendly preset"
        assert result.created_at == "2026-03-31T00:00:00+00:00"
        assert result.updated_at == "2026-03-31T00:00:00+00:00"

    async def test_get_returns_none_for_missing(
        self, repo: SQLitePersonalityPresetRepository
    ) -> None:
        result = await repo.get(NotBlankStr("nonexistent"))
        assert result is None

    async def test_save_upsert_updates_existing(
        self, repo: SQLitePersonalityPresetRepository
    ) -> None:
        config_json = _sample_config_json()
        await repo.save(
            Preset(
                name=NotBlankStr("my_preset"),
                config_json=config_json,
                description="Original",
                created_at="2026-03-31T00:00:00+00:00",
                updated_at="2026-03-31T00:00:00+00:00",
            )
        )
        updated_json = json.dumps({"traits": ["updated"]}, sort_keys=True)
        await repo.save(
            Preset(
                name=NotBlankStr("my_preset"),
                config_json=updated_json,
                description="Updated",
                created_at="2026-03-31T00:00:00+00:00",
                updated_at="2026-03-31T12:00:00+00:00",
            )
        )
        result = await repo.get(NotBlankStr("my_preset"))
        assert result is not None
        assert json.loads(result.config_json) == {"traits": ["updated"]}
        assert result.description == "Updated"
        assert result.created_at == "2026-03-31T00:00:00+00:00"
        assert result.updated_at == "2026-03-31T12:00:00+00:00"

    async def test_list_items_returns_sorted(
        self, repo: SQLitePersonalityPresetRepository
    ) -> None:
        config_json = _sample_config_json()
        await repo.save(
            Preset(
                name=NotBlankStr("zebra"),
                config_json=config_json,
                description="Z",
                created_at="2026-03-31T00:00:00+00:00",
                updated_at="2026-03-31T00:00:00+00:00",
            )
        )
        await repo.save(
            Preset(
                name=NotBlankStr("alpha"),
                config_json=config_json,
                description="A",
                created_at="2026-03-31T00:00:00+00:00",
                updated_at="2026-03-31T00:00:00+00:00",
            )
        )
        result = await repo.list_items()
        assert len(result) == 2
        assert result[0].name == "alpha"
        assert result[1].name == "zebra"

    async def test_list_items_empty(
        self, repo: SQLitePersonalityPresetRepository
    ) -> None:
        result = await repo.list_items()
        assert result == ()

    async def test_query_with_empty_spec(
        self, repo: SQLitePersonalityPresetRepository
    ) -> None:
        config_json = _sample_config_json()
        await repo.save(
            Preset(
                name=NotBlankStr("q1"),
                config_json=config_json,
                description="Q",
                created_at="2026-03-31T00:00:00+00:00",
                updated_at="2026-03-31T00:00:00+00:00",
            )
        )
        result = await repo.query(PresetFilterSpec())
        assert len(result) == 1
        assert result[0].name == "q1"

    async def test_delete_existing(
        self, repo: SQLitePersonalityPresetRepository
    ) -> None:
        config_json = _sample_config_json()
        await repo.save(
            Preset(
                name=NotBlankStr("my_preset"),
                config_json=config_json,
                description="desc",
                created_at="2026-03-31T00:00:00+00:00",
                updated_at="2026-03-31T00:00:00+00:00",
            )
        )
        deleted = await repo.delete(NotBlankStr("my_preset"))
        assert deleted is True
        assert await repo.get(NotBlankStr("my_preset")) is None

    async def test_delete_nonexistent(
        self, repo: SQLitePersonalityPresetRepository
    ) -> None:
        deleted = await repo.delete(NotBlankStr("nonexistent"))
        assert deleted is False

    async def test_count(self, repo: SQLitePersonalityPresetRepository) -> None:
        assert await repo.count(PresetFilterSpec()) == 0
        config_json = _sample_config_json()
        await repo.save(
            Preset(
                name=NotBlankStr("preset_a"),
                config_json=config_json,
                description="A",
                created_at="2026-03-31T00:00:00+00:00",
                updated_at="2026-03-31T00:00:00+00:00",
            )
        )
        await repo.save(
            Preset(
                name=NotBlankStr("preset_b"),
                config_json=config_json,
                description="B",
                created_at="2026-03-31T00:00:00+00:00",
                updated_at="2026-03-31T00:00:00+00:00",
            )
        )
        assert await repo.count(PresetFilterSpec()) == 2


@pytest.fixture
def unmigrated_repo(
    memory_db: aiosqlite.Connection,
) -> SQLitePersonalityPresetRepository:
    return SQLitePersonalityPresetRepository(
        memory_db, write_context=make_private_write_context()
    )


@pytest.mark.unit
class TestSQLitePersonalityPresetRepositoryErrors:
    """QueryError propagation when table does not exist."""

    @pytest.mark.parametrize(
        "method_call",
        [
            pytest.param(
                lambda r: r.save(
                    Preset(
                        name=NotBlankStr("x"),
                        config_json="{}",
                        description="",
                        created_at="2026-01-01T00:00:00+00:00",
                        updated_at="2026-01-01T00:00:00+00:00",
                    )
                ),
                id="save",
            ),
            pytest.param(lambda r: r.get(NotBlankStr("x")), id="get"),
            pytest.param(lambda r: r.list_items(), id="list_items"),
            pytest.param(lambda r: r.delete(NotBlankStr("x")), id="delete"),
            pytest.param(lambda r: r.count(PresetFilterSpec()), id="count"),
        ],
    )
    async def test_raises_query_error(
        self,
        unmigrated_repo: SQLitePersonalityPresetRepository,
        method_call: object,
    ) -> None:
        with pytest.raises(QueryError):
            await method_call(unmigrated_repo)  # type: ignore[operator]
