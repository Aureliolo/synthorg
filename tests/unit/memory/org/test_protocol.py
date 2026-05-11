"""Tests for OrgMemoryBackend protocol compliance."""

import pytest

from synthorg.memory.org.access_control import WriteAccessConfig
from synthorg.memory.org.hybrid_backend import HybridPromptRetrievalBackend
from synthorg.memory.org.protocol import OrgMemoryBackend
from synthorg.persistence.memory_protocol import OrgFactRepository
from tests._shared import mock_of


@pytest.mark.unit
class TestOrgMemoryBackendProtocol:
    """OrgMemoryBackend is runtime_checkable."""

    def test_hybrid_backend_is_instance(self) -> None:
        store = mock_of[OrgFactRepository]()
        backend = HybridPromptRetrievalBackend(
            core_policies=(),
            store=store,
            access_config=WriteAccessConfig(),
        )
        assert isinstance(backend, OrgMemoryBackend)
