"""Tests for memory backend factory."""

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from synthorg.memory.backends.mem0.adapter import Mem0MemoryBackend
from synthorg.memory.backends.mem0.config import Mem0EmbedderConfig
from synthorg.memory.config import (
    CompanyMemoryConfig,
    MemoryOptionsConfig,
    MemoryStorageConfig,
)
from synthorg.memory.errors import MemoryConfigError
from synthorg.memory.factory import create_memory_backend

pytestmark = pytest.mark.timeout(30)


def _test_embedder() -> Mem0EmbedderConfig:
    """Vendor-agnostic embedder config for tests."""
    return Mem0EmbedderConfig(
        provider="test-provider",
        model="test-embedding-001",
    )


@pytest.mark.unit
class TestCreateMemoryBackend:
    def test_mem0_creates_backend(self) -> None:
        config = CompanyMemoryConfig(backend="mem0")
        backend = create_memory_backend(config, embedder=_test_embedder())
        assert isinstance(backend, Mem0MemoryBackend)
        assert backend.is_connected is False
        assert backend.backend_name == "mem0"

    def test_mem0_passes_max_memories(self) -> None:
        config = CompanyMemoryConfig(
            backend="mem0",
            options=MemoryOptionsConfig(max_memories_per_agent=500),
        )
        backend = create_memory_backend(config, embedder=_test_embedder())
        assert isinstance(backend, Mem0MemoryBackend)
        assert backend.max_memories_per_agent == 500

    def test_unknown_backend_rejected_by_config_validation(self) -> None:
        """Unknown backends are rejected by config validation."""
        with pytest.raises(ValidationError, match="Unknown memory backend"):
            CompanyMemoryConfig(backend="nonexistent")

    def test_mem0_without_embedder_raises(self) -> None:
        config = CompanyMemoryConfig(backend="mem0")
        with pytest.raises(MemoryConfigError, match="requires an embedder"):
            create_memory_backend(config)

    def test_mem0_wrong_embedder_type_raises(self) -> None:
        config = CompanyMemoryConfig(backend="mem0")
        with pytest.raises(MemoryConfigError, match="must be a Mem0EmbedderConfig"):
            create_memory_backend(config, embedder="not-a-config")  # type: ignore[arg-type]

    def test_config_build_error_wraps_as_memory_config_error(self) -> None:
        """ValueError from build_config_from_company_config wraps."""
        storage = MemoryStorageConfig.model_construct(
            vector_store="chroma",
            history_store="sqlite",
            data_dir="/data/memory",
        )
        config = CompanyMemoryConfig.model_construct(
            backend="mem0",
            storage=storage,
        )
        with pytest.raises(MemoryConfigError, match="Invalid Mem0 configuration"):
            create_memory_backend(config, embedder=_test_embedder())

    def test_backend_init_value_error_wraps(self) -> None:
        """ValueError from Mem0MemoryBackend() constructor wraps."""
        config = CompanyMemoryConfig(backend="mem0")
        with (
            patch(
                "synthorg.memory.backends.mem0.Mem0MemoryBackend",
                side_effect=ValueError("init boom"),
            ),
            pytest.raises(MemoryConfigError, match="Failed to create Mem0"),
        ):
            create_memory_backend(config, embedder=_test_embedder())

    def test_backend_init_validation_error_wraps(self) -> None:
        """ValidationError from Mem0MemoryBackend() constructor wraps."""
        from pydantic import BaseModel

        class _Dummy(BaseModel):
            x: int

        try:
            _Dummy(x="not-an-int")  # type: ignore[arg-type]
        except ValidationError as ve:
            side_effect: ValidationError = ve

        config = CompanyMemoryConfig(backend="mem0")
        with (
            patch(
                "synthorg.memory.backends.mem0.Mem0MemoryBackend",
                side_effect=side_effect,
            ),
            pytest.raises(MemoryConfigError, match="Failed to create Mem0"),
        ):
            create_memory_backend(config, embedder=_test_embedder())

    def test_unknown_backend_bypassing_validation_raises(self) -> None:
        """Defensive guard when model_construct bypasses validation."""
        config = CompanyMemoryConfig.model_construct(
            backend="nonexistent",
        )
        with pytest.raises(MemoryConfigError, match="Unknown memory backend"):
            create_memory_backend(config, embedder=_test_embedder())
