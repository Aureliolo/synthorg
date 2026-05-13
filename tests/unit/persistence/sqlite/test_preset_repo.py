"""Tests for SQLitePersonalityPresetRepository."""

import json

import aiosqlite
import pytest

from synthorg.core.persistence_errors import QueryError
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
            "my_preset",
            config_json,
            "A friendly preset",
            "2026-03-31T00:00:00+00:00",
            "2026-03-31T00:00:00+00:00",
        )
        result = await repo.get("my_preset")
        assert result is not None
        cfg_json, description, created_at, updated_at = result
        assert json.loads(cfg_json) == json.loads(config_json)
        assert description == "A friendly preset"
        assert created_at == "2026-03-31T00:00:00+00:00"
        assert updated_at == "2026-03-31T00:00:00+00:00"

    async def test_get_returns_none_for_missing(
        self, repo: SQLitePersonalityPresetRepository
    ) -> None:
        result = await repo.get("nonexistent")
        assert result is None

    async def test_save_upsert_updates_existing(
        self, repo: SQLitePersonalityPresetRepository
    ) -> None:
        config_json = _sample_config_json()
        await repo.save(
            "my_preset",
            config_json,
            "Original",
            "2026-03-31T00:00:00+00:00",
            "2026-03-31T00:00:00+00:00",
        )
        updated_json = json.dumps({"traits": ["updated"]}, sort_keys=True)
        await repo.save(
            "my_preset",
            updated_json,
            "Updated",
            "2026-04-01T00:00:00+00:00",
            "2026-03-31T12:00:00+00:00",
        )
        result = await repo.get("my_preset")
        assert result is not None
        cfg_json, description, created_at, updated_at = result
        assert json.loads(cfg_json) == {"traits": ["updated"]}
        assert description == "Updated"
        assert created_at == "2026-03-31T00:00:00+00:00"
        assert updated_at == "2026-03-31T12:00:00+00:00"

    async def test_list_all_returns_sorted(
        self, repo: SQLitePersonalityPresetRepository
    ) -> None:
        config_json = _sample_config_json()
        await repo.save(
            "zebra",
            config_json,
            "Z",
            "2026-03-31T00:00:00+00:00",
            "2026-03-31T00:00:00+00:00",
        )
        await repo.save(
            "alpha",
            config_json,
            "A",
            "2026-03-31T00:00:00+00:00",
            "2026-03-31T00:00:00+00:00",
        )
        result = await repo.list_all()
        assert len(result) == 2
        assert result[0][0] == "alpha"
        assert result[1][0] == "zebra"

    async def test_list_all_empty(
        self, repo: SQLitePersonalityPresetRepository
    ) -> None:
        result = await repo.list_all()
        assert result == ()

    async def test_delete_existing(
        self, repo: SQLitePersonalityPresetRepository
    ) -> None:
        config_json = _sample_config_json()
        await repo.save(
            "my_preset",
            config_json,
            "desc",
            "2026-03-31T00:00:00+00:00",
            "2026-03-31T00:00:00+00:00",
        )
        deleted = await repo.delete("my_preset")
        assert deleted is True
        assert await repo.get("my_preset") is None

    async def test_delete_nonexistent(
        self, repo: SQLitePersonalityPresetRepository
    ) -> None:
        deleted = await repo.delete("nonexistent")
        assert deleted is False

    async def test_count(self, repo: SQLitePersonalityPresetRepository) -> None:
        assert await repo.count() == 0
        config_json = _sample_config_json()
        await repo.save(
            "preset_a",
            config_json,
            "A",
            "2026-03-31T00:00:00+00:00",
            "2026-03-31T00:00:00+00:00",
        )
        await repo.save(
            "preset_b",
            config_json,
            "B",
            "2026-03-31T00:00:00+00:00",
            "2026-03-31T00:00:00+00:00",
        )
        assert await repo.count() == 2


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
                    "x",
                    "{}",
                    "",
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                ),
                id="save",
            ),
            pytest.param(lambda r: r.get("x"), id="get"),
            pytest.param(lambda r: r.list_all(), id="list_all"),
            pytest.param(lambda r: r.delete("x"), id="delete"),
            pytest.param(lambda r: r.count(), id="count"),
        ],
    )
    async def test_raises_query_error(
        self,
        unmigrated_repo: SQLitePersonalityPresetRepository,
        method_call: object,
    ) -> None:
        with pytest.raises(QueryError):
            await method_call(unmigrated_repo)  # type: ignore[operator]
