"""Parametrized conformance tests for SettingsRepository.

Runs identically against the SQLite and Postgres backends via the
shared ``backend`` fixture in ``conftest.py``.
"""

import json
from datetime import UTC, datetime

import pytest

from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.settings_protocol import SettingRow


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
        assert await backend.settings.get((NS, NotBlankStr("missing"))) is None

    async def test_set_then_get_round_trip(
        self,
        backend: PersistenceBackend,
    ) -> None:
        entity = SettingRow(
            namespace=NS,
            key=NotBlankStr("k1"),
            value="v1",
            updated_at=_ts(2026, 4, 10),
        )
        await backend.settings.save(entity)
        result = await backend.settings.get((NS, NotBlankStr("k1")))
        assert result is not None
        assert result.value == "v1"
        assert datetime.fromisoformat(result.updated_at) == datetime(
            2026, 4, 10, 12, tzinfo=UTC
        )

    async def test_provider_config_metadata_blob_round_trip(
        self,
        backend: PersistenceBackend,
    ) -> None:
        # Provider configs persist as a JSON blob in the settings table;
        # the nested model metadata must survive the round trip.
        config = ProviderConfig(
            connection_name="conn-test",
            litellm_provider="test-provider",
            models=(
                ProviderModelConfig(
                    id="example-large-001",
                    metadata=ModelMetadata(
                        supports_vision=True,
                        family="example-large",
                        generation=2.0,
                        metadata_source="litellm",
                    ),
                ),
            ),
        )
        blob = json.dumps({"test": config.model_dump(mode="json")})
        await backend.settings.save(
            SettingRow(
                namespace=NotBlankStr("providers"),
                key=NotBlankStr("configs"),
                value=blob,
                updated_at=_ts(2026, 4, 11),
            ),
        )
        result = await backend.settings.get(
            (NotBlankStr("providers"), NotBlankStr("configs")),
        )
        assert result is not None
        restored = ProviderConfig.model_validate(json.loads(result.value)["test"])
        meta = restored.models[0].metadata
        assert meta.supports_vision is True
        assert meta.family == "example-large"
        assert meta.generation == 2.0
        assert meta.metadata_source == "litellm"

    async def test_set_upserts_existing_key(
        self,
        backend: PersistenceBackend,
    ) -> None:
        entity1 = SettingRow(
            namespace=NS,
            key=NotBlankStr("k2"),
            value="initial",
            updated_at=_ts(2026, 1, 1),
        )
        await backend.settings.save(entity1)
        entity2 = SettingRow(
            namespace=NS,
            key=NotBlankStr("k2"),
            value="updated",
            updated_at=_ts(2026, 2, 1),
        )
        await backend.settings.save(entity2)
        result = await backend.settings.get((NS, NotBlankStr("k2")))
        assert result is not None
        assert result.value == "updated"


@pytest.mark.integration
class TestSettingsCompareAndSwap:
    async def test_cas_empty_string_inserts_new(
        self,
        backend: PersistenceBackend,
    ) -> None:
        entity = SettingRow(
            namespace=NS,
            key=NotBlankStr("cas_new"),
            value="first",
            updated_at=_ts(2026, 1, 1),
        )
        ok = await backend.settings.set_if_unchanged(entity, expected_updated_at="")
        assert ok is True
        result = await backend.settings.get((NS, NotBlankStr("cas_new")))
        assert result is not None
        assert result.value == "first"

    async def test_cas_empty_string_rejects_existing(
        self,
        backend: PersistenceBackend,
    ) -> None:
        entity1 = SettingRow(
            namespace=NS,
            key=NotBlankStr("cas_exist"),
            value="first",
            updated_at=_ts(2026, 1, 1),
        )
        await backend.settings.save(entity1)
        entity2 = SettingRow(
            namespace=NS,
            key=NotBlankStr("cas_exist"),
            value="second",
            updated_at=_ts(2026, 2, 1),
        )
        ok = await backend.settings.set_if_unchanged(entity2, expected_updated_at="")
        assert ok is False
        result = await backend.settings.get((NS, NotBlankStr("cas_exist")))
        assert result is not None
        assert result.value == "first"

    async def test_cas_matching_updates(
        self,
        backend: PersistenceBackend,
    ) -> None:
        entity1 = SettingRow(
            namespace=NS,
            key=NotBlankStr("cas_m"),
            value="v1",
            updated_at=_ts(2026, 1, 1),
        )
        await backend.settings.save(entity1)
        current = await backend.settings.get((NS, NotBlankStr("cas_m")))
        assert current is not None
        entity2 = SettingRow(
            namespace=NS,
            key=NotBlankStr("cas_m"),
            value="v2",
            updated_at=_ts(2026, 2, 1),
        )
        ok = await backend.settings.set_if_unchanged(
            entity2, expected_updated_at=current.updated_at
        )
        assert ok is True

    async def test_cas_mismatch_rejects(
        self,
        backend: PersistenceBackend,
    ) -> None:
        entity1 = SettingRow(
            namespace=NS,
            key=NotBlankStr("cas_mm"),
            value="v1",
            updated_at=_ts(2026, 1, 1),
        )
        await backend.settings.save(entity1)
        entity2 = SettingRow(
            namespace=NS,
            key=NotBlankStr("cas_mm"),
            value="v2",
            updated_at=_ts(2026, 2, 1),
        )
        ok = await backend.settings.set_if_unchanged(
            entity2, expected_updated_at=_ts(2020, 1, 1)
        )
        assert ok is False
        result = await backend.settings.get((NS, NotBlankStr("cas_mm")))
        assert result is not None
        assert result.value == "v1"


@pytest.mark.integration
class TestSettingsListAndDelete:
    async def test_get_namespace_returns_sorted_by_key(
        self,
        backend: PersistenceBackend,
    ) -> None:
        entity1 = SettingRow(
            namespace=NS,
            key=NotBlankStr("b_key"),
            value="b_val",
            updated_at=_ts(2026, 1, 1),
        )
        await backend.settings.save(entity1)
        entity2 = SettingRow(
            namespace=NS,
            key=NotBlankStr("a_key"),
            value="a_val",
            updated_at=_ts(2026, 1, 1),
        )
        await backend.settings.save(entity2)
        entity3 = SettingRow(
            namespace=NS_OTHER,
            key=NotBlankStr("x_key"),
            value="x_val",
            updated_at=_ts(2026, 1, 1),
        )
        await backend.settings.save(entity3)

        result = await backend.settings.get_namespace(NS)
        assert len(result) == 2
        assert result[0].key == "a_key"
        assert result[1].key == "b_key"

    async def test_list_items_returns_all_namespaces(
        self,
        backend: PersistenceBackend,
    ) -> None:
        entity1 = SettingRow(
            namespace=NS,
            key=NotBlankStr("k"),
            value="v",
            updated_at=_ts(2026, 1, 1),
        )
        await backend.settings.save(entity1)
        entity2 = SettingRow(
            namespace=NS_OTHER,
            key=NotBlankStr("k"),
            value="v",
            updated_at=_ts(2026, 1, 1),
        )
        await backend.settings.save(entity2)
        result = await backend.settings.list_items()
        namespaces = {row.namespace for row in result}
        assert "test_ns" in namespaces
        assert "other_ns" in namespaces

    async def test_delete_returns_true_when_present(
        self,
        backend: PersistenceBackend,
    ) -> None:
        entity = SettingRow(
            namespace=NS,
            key=NotBlankStr("k"),
            value="v",
            updated_at=_ts(2026, 1, 1),
        )
        await backend.settings.save(entity)
        assert await backend.settings.delete((NS, NotBlankStr("k"))) is True
        assert await backend.settings.get((NS, NotBlankStr("k"))) is None

    async def test_delete_returns_false_when_missing(
        self,
        backend: PersistenceBackend,
    ) -> None:
        assert await backend.settings.delete((NS, NotBlankStr("missing"))) is False

    async def test_delete_namespace_removes_all_keys(
        self,
        backend: PersistenceBackend,
    ) -> None:
        entity1 = SettingRow(
            namespace=NS,
            key=NotBlankStr("k1"),
            value="v1",
            updated_at=_ts(2026, 1, 1),
        )
        await backend.settings.save(entity1)
        entity2 = SettingRow(
            namespace=NS,
            key=NotBlankStr("k2"),
            value="v2",
            updated_at=_ts(2026, 1, 1),
        )
        await backend.settings.save(entity2)
        entity3 = SettingRow(
            namespace=NS_OTHER,
            key=NotBlankStr("k"),
            value="v",
            updated_at=_ts(2026, 1, 1),
        )
        await backend.settings.save(entity3)

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
        entity1 = SettingRow(
            namespace=NS,
            key=NotBlankStr("k1"),
            value="v1",
            updated_at=_ts(2026, 1, 1),
        )
        await backend.settings.save(entity1)
        entity2 = SettingRow(
            namespace=NS,
            key=NotBlankStr("k2"),
            value="v2",
            updated_at=_ts(2026, 1, 1),
        )
        await backend.settings.save(entity2)
        entity3 = SettingRow(
            namespace=NS_OTHER,
            key=NotBlankStr("k"),
            value="v",
            updated_at=_ts(2026, 1, 1),
        )
        await backend.settings.save(entity3)

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
