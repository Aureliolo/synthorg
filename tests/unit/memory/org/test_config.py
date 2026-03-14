"""Tests for org memory configuration."""

import pytest
from pydantic import ValidationError

from synthorg.memory.org.config import (
    ExtendedStoreConfig,
    OrgMemoryConfig,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestExtendedStoreConfig:
    """ExtendedStoreConfig defaults and validation."""

    def test_defaults(self) -> None:
        config = ExtendedStoreConfig()
        assert config.backend == "sqlite"
        assert config.max_retrieved_per_query == 5

    def test_unknown_backend_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Unknown extended store backend"):
            ExtendedStoreConfig(backend="postgres")

    def test_max_retrieved_bounds(self) -> None:
        low = ExtendedStoreConfig(max_retrieved_per_query=1)
        assert low.max_retrieved_per_query == 1
        high = ExtendedStoreConfig(max_retrieved_per_query=50)
        assert high.max_retrieved_per_query == 50
        with pytest.raises(ValidationError):
            ExtendedStoreConfig(max_retrieved_per_query=0)
        with pytest.raises(ValidationError):
            ExtendedStoreConfig(max_retrieved_per_query=51)


@pytest.mark.unit
class TestOrgMemoryConfig:
    """OrgMemoryConfig defaults and validation."""

    def test_defaults(self) -> None:
        config = OrgMemoryConfig()
        assert config.backend == "hybrid_prompt_retrieval"
        assert config.core_policies == ()
        assert config.extended_store.backend == "sqlite"

    def test_unknown_backend_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Unknown org memory backend"):
            OrgMemoryConfig(backend="unknown")

    def test_core_policies(self) -> None:
        config = OrgMemoryConfig(
            core_policies=("All code must be reviewed", "No direct production access"),
        )
        assert len(config.core_policies) == 2
