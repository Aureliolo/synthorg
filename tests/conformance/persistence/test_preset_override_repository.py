"""Parametrized conformance tests for ``PresetOverrideRepo``.

Runs against both SQLite and Postgres via the ``backend`` fixture so
SQLite-vs-Postgres divergence (TEXT-vs-JSONB columns, ISO-string vs
TIMESTAMPTZ) is caught on every commit.
"""

from datetime import UTC, date, datetime

import pytest

from synthorg.api.dto_provider_capabilities import PresetOverride
from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.schema import ProviderModelConfig
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.enums import AuthType

pytestmark = pytest.mark.integration


def _override(
    preset_name: str = "test-cloud-provider",
    *,
    base_url: str | None = "https://api.example.com/v1",
    candidate_urls: tuple[str, ...] | None = None,
    default_models: tuple[ProviderModelConfig, ...] | None = None,
    supported_auth_types: tuple[AuthType, ...] | None = None,
) -> PresetOverride:
    return PresetOverride(
        preset_name=preset_name,
        base_url=base_url,
        candidate_urls=candidate_urls,
        default_models=default_models,
        supported_auth_types=supported_auth_types,
        updated_at=datetime.now(UTC),
        updated_by="user-1",
    )


async def test_get_when_missing_returns_none(backend: PersistenceBackend) -> None:
    repo = backend.preset_overrides
    result = await repo.get("does-not-exist")
    assert result is None


async def test_save_then_get(backend: PersistenceBackend) -> None:
    repo = backend.preset_overrides
    record = _override()
    await repo.save(record)
    loaded = await repo.get("test-cloud-provider")
    assert loaded is not None
    assert loaded.preset_name == "test-cloud-provider"
    assert loaded.base_url == record.base_url


async def test_save_replaces_existing(backend: PersistenceBackend) -> None:
    repo = backend.preset_overrides
    await repo.save(_override(base_url="https://first.example.com/v1"))
    await repo.save(_override(base_url="https://second.example.com/v1"))
    loaded = await repo.get("test-cloud-provider")
    assert loaded is not None
    assert loaded.base_url == "https://second.example.com/v1"


async def test_delete_existing(backend: PersistenceBackend) -> None:
    repo = backend.preset_overrides
    await repo.save(_override())
    removed = await repo.delete("test-cloud-provider")
    assert removed is True
    assert await repo.get("test-cloud-provider") is None


async def test_delete_missing_returns_false(backend: PersistenceBackend) -> None:
    repo = backend.preset_overrides
    removed = await repo.delete("never-existed")
    assert removed is False


async def test_round_trip_models_list(backend: PersistenceBackend) -> None:
    repo = backend.preset_overrides
    models = (
        ProviderModelConfig(id="example-large-001", alias="large"),
        ProviderModelConfig(id="example-small-001", alias="small"),
    )
    await repo.save(_override(default_models=models))
    loaded = await repo.get("test-cloud-provider")
    assert loaded is not None
    assert loaded.default_models is not None
    assert [m.id for m in loaded.default_models] == [
        "example-large-001",
        "example-small-001",
    ]


async def test_round_trip_model_metadata(backend: PersistenceBackend) -> None:
    repo = backend.preset_overrides
    models = (
        ProviderModelConfig(
            id="example-large-001",
            alias="large",
            metadata=ModelMetadata(
                supports_tools=True,
                supports_vision=True,
                supports_reasoning=True,
                max_output_tokens=8192,
                family="example-large",
                generation=2.0,
                release_date=date(2025, 5, 14),
                metadata_source="preset",
            ),
        ),
    )
    await repo.save(_override(default_models=models))
    loaded = await repo.get("test-cloud-provider")
    assert loaded is not None
    assert loaded.default_models is not None
    meta = loaded.default_models[0].metadata
    assert meta.supports_tools is True
    assert meta.supports_vision is True
    assert meta.supports_reasoning is True
    assert meta.max_output_tokens == 8192
    assert meta.family == "example-large"
    assert meta.generation == 2.0
    assert meta.release_date == date(2025, 5, 14)
    assert meta.metadata_source == "preset"


async def test_legacy_model_without_metadata_defaults(
    backend: PersistenceBackend,
) -> None:
    repo = backend.preset_overrides
    models = (ProviderModelConfig(id="example-small-001"),)
    await repo.save(_override(default_models=models))
    loaded = await repo.get("test-cloud-provider")
    assert loaded is not None
    assert loaded.default_models is not None
    # A model persisted without metadata loads the default record.
    assert loaded.default_models[0].metadata == ModelMetadata()


async def test_round_trip_candidate_urls_list(backend: PersistenceBackend) -> None:
    repo = backend.preset_overrides
    urls = ("http://localhost:11434", "http://10.0.0.5:11434")
    await repo.save(
        _override(
            preset_name="test-local-provider",
            base_url=None,
            candidate_urls=urls,
        ),
    )
    loaded = await repo.get("test-local-provider")
    assert loaded is not None
    assert loaded.candidate_urls == urls


async def test_round_trip_supported_auth_types(backend: PersistenceBackend) -> None:
    repo = backend.preset_overrides
    auth_types = (AuthType.API_KEY, AuthType.SUBSCRIPTION)
    await repo.save(_override(supported_auth_types=auth_types))
    loaded = await repo.get("test-cloud-provider")
    assert loaded is not None
    assert loaded.supported_auth_types == auth_types


async def test_list_items_orders_by_preset_name(backend: PersistenceBackend) -> None:
    repo = backend.preset_overrides
    await repo.save(_override(preset_name="zeta-provider"))
    await repo.save(_override(preset_name="alpha-provider"))

    page = await repo.list_items(limit=10)
    names = [row.preset_name for row in page]
    assert names == sorted(names)


_T0 = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
_T1 = datetime(2026, 5, 20, 12, 5, tzinfo=UTC)


def _override_at(updated_at: datetime, *, base_url: str) -> PresetOverride:
    return _override(base_url=base_url).model_copy(update={"updated_at": updated_at})


async def test_save_if_unchanged_inserts_when_absent(
    backend: PersistenceBackend,
) -> None:
    repo = backend.preset_overrides
    written = await repo.save_if_unchanged(
        _override_at(_T0, base_url="https://fresh.example.com/v1"),
        expected_updated_at=None,
    )
    assert written is True
    loaded = await repo.get("test-cloud-provider")
    assert loaded is not None
    assert loaded.updated_at == _T0


async def test_save_if_unchanged_rejects_insert_when_row_appeared(
    backend: PersistenceBackend,
) -> None:
    repo = backend.preset_overrides
    await repo.save(_override(base_url="https://winner.example.com/v1"))
    # A second writer that read "no row" must lose to the row that landed.
    written = await repo.save_if_unchanged(
        _override_at(_T1, base_url="https://loser.example.com/v1"),
        expected_updated_at=None,
    )
    assert written is False
    loaded = await repo.get("test-cloud-provider")
    assert loaded is not None
    assert loaded.base_url == "https://winner.example.com/v1"


async def test_save_if_unchanged_updates_on_matching_timestamp(
    backend: PersistenceBackend,
) -> None:
    repo = backend.preset_overrides
    await repo.save(_override_at(_T0, base_url="https://old.example.com/v1"))
    written = await repo.save_if_unchanged(
        _override_at(_T1, base_url="https://new.example.com/v1"),
        expected_updated_at=_T0,
    )
    assert written is True
    loaded = await repo.get("test-cloud-provider")
    assert loaded is not None
    assert loaded.base_url == "https://new.example.com/v1"
    assert loaded.updated_at == _T1


async def test_save_if_unchanged_rejects_stale_timestamp(
    backend: PersistenceBackend,
) -> None:
    repo = backend.preset_overrides
    # The stored row has moved on to _T1; a writer that read _T0 is stale.
    await repo.save(_override_at(_T1, base_url="https://current.example.com/v1"))
    written = await repo.save_if_unchanged(
        _override_at(_T0, base_url="https://stale.example.com/v1"),
        expected_updated_at=_T0,
    )
    assert written is False
    loaded = await repo.get("test-cloud-provider")
    assert loaded is not None
    assert loaded.base_url == "https://current.example.com/v1"
