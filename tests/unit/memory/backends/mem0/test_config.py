"""Tests for Mem0 backend configuration."""

import pytest
from pydantic import ValidationError

from ai_company.memory.backends.mem0.config import (
    Mem0BackendConfig,
    Mem0EmbedderConfig,
    build_config_from_company_config,
    build_mem0_config_dict,
)
from ai_company.memory.config import CompanyMemoryConfig

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestMem0EmbedderConfig:
    def test_defaults(self) -> None:
        config = Mem0EmbedderConfig()
        assert config.provider == "openai"
        assert config.model == "text-embedding-3-small"
        assert config.dims == 1536

    def test_custom_values(self) -> None:
        config = Mem0EmbedderConfig(
            provider="test-provider",
            model="test-embedding-001",
            dims=768,
        )
        assert config.provider == "test-provider"
        assert config.model == "test-embedding-001"
        assert config.dims == 768

    def test_frozen(self) -> None:
        config = Mem0EmbedderConfig()
        with pytest.raises(ValidationError):
            config.dims = 512  # type: ignore[misc]

    def test_rejects_zero_dims(self) -> None:
        with pytest.raises(ValidationError, match="dims"):
            Mem0EmbedderConfig(dims=0)

    def test_rejects_blank_provider(self) -> None:
        with pytest.raises(ValidationError):
            Mem0EmbedderConfig(provider="   ")


@pytest.mark.unit
class TestMem0BackendConfig:
    def test_defaults(self) -> None:
        config = Mem0BackendConfig()
        assert config.data_dir == "/data/memory"
        assert config.collection_name == "synthorg_memories"
        assert config.embedder.provider == "openai"

    def test_custom_data_dir(self) -> None:
        config = Mem0BackendConfig(data_dir="/tmp/test-memory")  # noqa: S108
        assert config.data_dir == "/tmp/test-memory"  # noqa: S108

    def test_custom_collection(self) -> None:
        config = Mem0BackendConfig(collection_name="test-collection")
        assert config.collection_name == "test-collection"

    def test_frozen(self) -> None:
        config = Mem0BackendConfig()
        with pytest.raises(ValidationError):
            config.data_dir = "/other"  # type: ignore[misc]


@pytest.mark.unit
class TestBuildMem0ConfigDict:
    def test_default_config(self) -> None:
        config = Mem0BackendConfig()
        result = build_mem0_config_dict(config)

        assert result["vector_store"]["provider"] == "qdrant"
        assert (
            result["vector_store"]["config"]["collection_name"] == "synthorg_memories"
        )
        assert result["vector_store"]["config"]["embedding_model_dims"] == 1536
        assert result["vector_store"]["config"]["path"] == "/data/memory/qdrant"
        assert result["embedder"]["provider"] == "openai"
        assert result["embedder"]["config"]["model"] == "text-embedding-3-small"
        assert result["history_db_path"] == "/data/memory/history.db"
        assert result["version"] == "v1.1"

    def test_custom_config(self) -> None:
        config = Mem0BackendConfig(
            data_dir="/custom/path",
            collection_name="custom-col",
            embedder=Mem0EmbedderConfig(
                provider="test-provider",
                model="test-model",
                dims=384,
            ),
        )
        result = build_mem0_config_dict(config)

        assert result["vector_store"]["config"]["path"] == "/custom/path/qdrant"
        assert result["vector_store"]["config"]["collection_name"] == "custom-col"
        assert result["vector_store"]["config"]["embedding_model_dims"] == 384
        assert result["embedder"]["provider"] == "test-provider"
        assert result["embedder"]["config"]["model"] == "test-model"
        assert result["history_db_path"] == "/custom/path/history.db"


@pytest.mark.unit
class TestBuildConfigFromCompanyConfig:
    def test_derives_data_dir(self) -> None:
        company_config = CompanyMemoryConfig(
            backend="mem0",
        )
        mem0_config = build_config_from_company_config(company_config)

        assert mem0_config.data_dir == company_config.storage.data_dir

    def test_custom_data_dir(self) -> None:
        company_config = CompanyMemoryConfig(
            backend="mem0",
            storage={"data_dir": "/custom/data"},
        )
        mem0_config = build_config_from_company_config(company_config)

        assert mem0_config.data_dir == "/custom/data"
