"""Tests for memory configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.core.enums import ConsolidationInterval, MemoryLevel
from synthorg.memory.config import (
    CompanyMemoryConfig,
    EmbedderOverrideConfig,
    MemoryOptionsConfig,
    MemoryStorageConfig,
)
from synthorg.memory.retrieval_config import MemoryRetrievalConfig

# ── MemoryStorageConfig ──────────────────────────────────────────


@pytest.mark.unit
class TestMemoryStorageConfig:
    def test_defaults(self) -> None:
        c = MemoryStorageConfig()
        assert c.data_dir == "/data/memory"
        assert c.vector_store == "qdrant"
        assert c.history_store == "sqlite"

    def test_custom_values(self) -> None:
        c = MemoryStorageConfig(
            data_dir="/custom/path",
            vector_store="qdrant-external",
            history_store="postgresql",
        )
        assert c.data_dir == "/custom/path"
        assert c.vector_store == "qdrant-external"

    def test_frozen(self) -> None:
        c = MemoryStorageConfig()
        with pytest.raises(ValidationError):
            c.data_dir = "/other"  # type: ignore[misc]

    def test_path_traversal_rejected(self) -> None:
        with pytest.raises(ValidationError, match="traversal"):
            MemoryStorageConfig(data_dir="/data/../etc/passwd")

    @pytest.mark.parametrize(
        "bad_path",
        [
            "/data/sub/../../../etc",
            "/data/..",
            "..",
            "data/../secret",
            "C:\\data\\..\\secret",
            "data\\..\\..\\etc",
        ],
    )
    def test_path_traversal_variants_rejected(self, bad_path: str) -> None:
        with pytest.raises(ValidationError, match="traversal"):
            MemoryStorageConfig(data_dir=bad_path)

    def test_dotdot_substring_in_segment_accepted(self) -> None:
        """Paths with '..' as a substring (e.g. '..hidden') are valid."""
        c = MemoryStorageConfig(data_dir="/data/..hidden/memory")
        assert c.data_dir == "/data/..hidden/memory"

    def test_absolute_path_accepted(self) -> None:
        c = MemoryStorageConfig(data_dir="/var/data/memory")
        assert c.data_dir == "/var/data/memory"

    def test_empty_data_dir_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least 1 character"):
            MemoryStorageConfig(data_dir="")

    def test_whitespace_data_dir_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            MemoryStorageConfig(data_dir="   ")

    @pytest.mark.parametrize(
        "store",
        ["qdrant", "qdrant-external"],
    )
    def test_valid_vector_stores_accepted(self, store: str) -> None:
        c = MemoryStorageConfig(vector_store=store)
        assert c.vector_store == store

    def test_unknown_vector_store_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Unknown vector_store"):
            MemoryStorageConfig(vector_store="invalid-store")

    @pytest.mark.parametrize(
        "store",
        ["sqlite", "postgresql"],
    )
    def test_valid_history_stores_accepted(self, store: str) -> None:
        c = MemoryStorageConfig(history_store=store)
        assert c.history_store == store

    def test_unknown_history_store_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Unknown history_store"):
            MemoryStorageConfig(history_store="invalid-store")


# ── MemoryOptionsConfig ─────────────────────────────────────────


@pytest.mark.unit
class TestMemoryOptionsConfig:
    def test_defaults(self) -> None:
        c = MemoryOptionsConfig()
        assert c.retention_days is None
        assert c.max_memories_per_agent == 10_000
        assert c.consolidation_interval is ConsolidationInterval.DAILY
        assert c.shared_knowledge_base is True

    def test_custom_values(self) -> None:
        c = MemoryOptionsConfig(
            retention_days=90,
            max_memories_per_agent=5000,
            consolidation_interval=ConsolidationInterval.WEEKLY,
            shared_knowledge_base=False,
        )
        assert c.retention_days == 90
        assert c.max_memories_per_agent == 5000
        assert c.consolidation_interval is ConsolidationInterval.WEEKLY
        assert c.shared_knowledge_base is False

    def test_frozen(self) -> None:
        c = MemoryOptionsConfig()
        with pytest.raises(ValidationError):
            c.retention_days = 30  # type: ignore[misc]

    def test_retention_days_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryOptionsConfig(retention_days=0)

    def test_retention_days_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryOptionsConfig(retention_days=-1)

    def test_retention_days_minimum_accepted(self) -> None:
        c = MemoryOptionsConfig(retention_days=1)
        assert c.retention_days == 1

    def test_max_memories_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryOptionsConfig(max_memories_per_agent=0)

    def test_max_memories_minimum_accepted(self) -> None:
        c = MemoryOptionsConfig(max_memories_per_agent=1)
        assert c.max_memories_per_agent == 1

    def test_consolidation_interval_all_values(self) -> None:
        for interval in ConsolidationInterval:
            c = MemoryOptionsConfig(consolidation_interval=interval)
            assert c.consolidation_interval is interval


# ── EmbedderOverrideConfig ──────────────────────────────────────


@pytest.mark.unit
class TestEmbedderOverrideConfig:
    def test_defaults_all_none(self) -> None:
        c = EmbedderOverrideConfig()
        assert c.provider is None
        assert c.model is None
        assert c.dims is None

    def test_provider_only(self) -> None:
        c = EmbedderOverrideConfig(provider="test-provider")
        assert c.provider == "test-provider"
        assert c.model is None
        assert c.dims is None

    def test_model_requires_dims(self) -> None:
        with pytest.raises(ValidationError, match="dims"):
            EmbedderOverrideConfig(model="test-model")

    def test_model_with_dims_accepted(self) -> None:
        c = EmbedderOverrideConfig(
            model="test-model",
            dims=768,
        )
        assert c.model == "test-model"
        assert c.dims == 768

    def test_full_override(self) -> None:
        c = EmbedderOverrideConfig(
            provider="test-provider",
            model="test-model",
            dims=3584,
        )
        assert c.provider == "test-provider"
        assert c.model == "test-model"
        assert c.dims == 3584

    def test_frozen(self) -> None:
        c = EmbedderOverrideConfig()
        with pytest.raises(ValidationError):
            c.dims = 512  # type: ignore[misc]

    def test_rejects_blank_provider(self) -> None:
        with pytest.raises(ValidationError):
            EmbedderOverrideConfig(provider="   ")

    def test_rejects_blank_model(self) -> None:
        with pytest.raises(ValidationError):
            EmbedderOverrideConfig(model="   ", dims=768)

    def test_rejects_zero_dims(self) -> None:
        with pytest.raises(ValidationError):
            EmbedderOverrideConfig(model="test-model", dims=0)

    def test_rejects_negative_dims(self) -> None:
        with pytest.raises(ValidationError):
            EmbedderOverrideConfig(model="test-model", dims=-1)

    def test_dims_without_model_rejected(self) -> None:
        """dims-only is rejected (dimensions are model-dependent)."""
        with pytest.raises(ValidationError, match="model"):
            EmbedderOverrideConfig(dims=1024)


# ── CompanyMemoryConfig ──────────────────────────────────────────


@pytest.mark.unit
class TestCompanyMemoryConfig:
    def test_defaults(self) -> None:
        c = CompanyMemoryConfig()
        assert c.backend == "mem0"
        # ``memory.default_level`` is registered with default ``persistent``;
        # the mirror validator surfaces that value through the Pydantic field.
        assert c.level is MemoryLevel.PERSISTENT
        assert isinstance(c.storage, MemoryStorageConfig)
        assert isinstance(c.options, MemoryOptionsConfig)
        assert isinstance(c.retrieval, MemoryRetrievalConfig)

    def test_valid_backend_accepted(self) -> None:
        c = CompanyMemoryConfig(backend="mem0")
        assert c.backend == "mem0"

    def test_unknown_backend_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Unknown memory backend"):
            CompanyMemoryConfig(backend="nonexistent")

    def test_frozen(self) -> None:
        c = CompanyMemoryConfig()
        with pytest.raises(ValidationError):
            c.backend = "other"  # type: ignore[misc]

    def test_all_memory_levels(self) -> None:
        for level in MemoryLevel:
            c = CompanyMemoryConfig(level=level)
            assert c.level is level

    def test_custom_nested_config(self) -> None:
        c = CompanyMemoryConfig(
            backend="mem0",
            level=MemoryLevel.PERSISTENT,
            storage=MemoryStorageConfig(data_dir="/custom"),
            options=MemoryOptionsConfig(retention_days=30),
        )
        assert c.storage.data_dir == "/custom"
        assert c.options.retention_days == 30

    def test_json_roundtrip(self) -> None:
        c = CompanyMemoryConfig(
            backend="mem0",
            level=MemoryLevel.PERSISTENT,
            options=MemoryOptionsConfig(retention_days=60),
        )
        json_str = c.model_dump_json()
        restored = CompanyMemoryConfig.model_validate_json(json_str)
        assert restored == c

    def test_embedder_default_none(self) -> None:
        c = CompanyMemoryConfig()
        assert c.embedder is None

    def test_embedder_override(self) -> None:
        override = EmbedderOverrideConfig(
            provider="test-provider",
            model="test-model",
            dims=768,
        )
        c = CompanyMemoryConfig(embedder=override)
        assert c.embedder is not None
        assert c.embedder.model == "test-model"

    def test_default_includes_procedural_config(self) -> None:
        c = CompanyMemoryConfig()
        assert c.procedural is not None
        assert c.procedural.enabled is True
        assert c.procedural.model == "example-small-001"

    def test_custom_retrieval_config(self) -> None:
        c = CompanyMemoryConfig(
            retrieval=MemoryRetrievalConfig(
                relevance_weight=0.6,
                recency_weight=0.4,
                max_memories=50,
            ),
        )
        assert c.retrieval.relevance_weight == 0.6
        assert c.retrieval.max_memories == 50
