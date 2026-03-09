"""Tests for memory backend factory."""

import pytest
from pydantic import ValidationError

from ai_company.memory.config import CompanyMemoryConfig
from ai_company.memory.errors import MemoryConfigError
from ai_company.memory.factory import create_memory_backend

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestCreateMemoryBackend:
    def test_mem0_raises_not_yet_implemented(self) -> None:
        config = CompanyMemoryConfig(backend="mem0")
        with pytest.raises(MemoryConfigError, match="not yet implemented"):
            create_memory_backend(config)

    def test_unknown_backend_rejected_by_config_validation(self) -> None:
        """Unknown backends are rejected by config validation."""
        with pytest.raises(ValidationError, match="Unknown memory backend"):
            CompanyMemoryConfig(backend="nonexistent")
