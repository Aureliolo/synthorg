"""Tests for memory backend factory."""

import pytest
from pydantic import ValidationError

from ai_company.memory.backends.mem0.adapter import Mem0MemoryBackend
from ai_company.memory.config import CompanyMemoryConfig
from ai_company.memory.factory import create_memory_backend

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestCreateMemoryBackend:
    def test_mem0_creates_backend(self) -> None:
        config = CompanyMemoryConfig(backend="mem0")
        backend = create_memory_backend(config)
        assert isinstance(backend, Mem0MemoryBackend)
        assert backend.is_connected is False
        assert backend.backend_name == "mem0"

    def test_mem0_passes_max_memories(self) -> None:
        config = CompanyMemoryConfig(
            backend="mem0",
            options={"max_memories_per_agent": 500},
        )
        backend = create_memory_backend(config)
        assert isinstance(backend, Mem0MemoryBackend)
        assert backend.max_memories_per_agent == 500

    def test_unknown_backend_rejected_by_config_validation(self) -> None:
        """Unknown backends are rejected by config validation."""
        with pytest.raises(ValidationError, match="Unknown memory backend"):
            CompanyMemoryConfig(backend="nonexistent")
