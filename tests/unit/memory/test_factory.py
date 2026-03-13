"""Tests for memory backend factory."""

import pytest
from pydantic import ValidationError

from ai_company.memory.backends.mem0.adapter import Mem0MemoryBackend
from ai_company.memory.backends.mem0.config import Mem0EmbedderConfig
from ai_company.memory.config import CompanyMemoryConfig, MemoryOptionsConfig
from ai_company.memory.errors import MemoryConfigError
from ai_company.memory.factory import create_memory_backend

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
