"""Tests for OrgMemoryBackend protocol compliance."""

from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from synthorg.core.types import NotBlankStr
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


@pytest.mark.unit
class TestCorePolicyIdDeterminism:
    """Synthetic core-policy ids are deterministic uuid5, not random."""

    async def test_core_policy_ids_stable_across_calls(self) -> None:
        store = mock_of[OrgFactRepository](
            list_by_category=AsyncMock(return_value=()),
        )
        backend = HybridPromptRetrievalBackend(
            core_policies=(
                NotBlankStr("Ship on Tuesdays."),
                NotBlankStr("Be kind in review."),
            ),
            store=store,
            access_config=WriteAccessConfig(),
        )
        await backend.connect()

        first = await backend.list_policies()
        second = await backend.list_policies()

        # uuid5 yields the SAME id for a given index on every call; a
        # uuid4 default_factory would differ between the two calls.
        assert [f.id for f in first] == [f.id for f in second]
        assert all(isinstance(f.id, UUID) for f in first)
        # Distinct policies keep distinct ids (per-index uuid5 seed).
        assert len({f.id for f in first}) == len(first)
