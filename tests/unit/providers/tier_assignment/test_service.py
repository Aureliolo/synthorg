"""Unit tests for the tier-assignment service (heuristic + overrides)."""

from typing import override

import pytest

from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.providers.enums import AuthType
from synthorg.providers.tier_assignment.models import TierAssignmentMap
from synthorg.providers.tier_assignment.service import (
    TierAssignmentService,
    TierOverrideStore,
)
from tests._shared import FakeClock

pytestmark = pytest.mark.unit


class _MemoryStore(TierOverrideStore):
    """In-memory override store for tests."""

    def __init__(self) -> None:
        self._map = TierAssignmentMap()

    @override
    async def load(self) -> TierAssignmentMap:
        return self._map

    @override
    async def save(self, overrides: TierAssignmentMap) -> None:
        self._map = overrides


def _providers() -> dict[str, ProviderConfig]:
    return {
        "local-host": ProviderConfig(
            auth_type=AuthType.NONE,
            models=(
                ProviderModelConfig(
                    id="tiny-7b",
                    metadata=ModelMetadata(
                        parameter_count=7_000_000_000,
                        metadata_source="probe",
                    ),
                ),
                ProviderModelConfig(
                    id="huge-120b",
                    metadata=ModelMetadata(
                        parameter_count=120_000_000_000,
                        metadata_source="probe",
                    ),
                ),
            ),
        ),
    }


async def test_effective_assignments_are_heuristic_without_overrides() -> None:
    service = TierAssignmentService(store=_MemoryStore(), clock=FakeClock())
    assignments = await service.effective_assignments(_providers())

    by_id = {a.model_id: a for a in assignments}
    assert by_id["tiny-7b"].tier == "small"
    assert by_id["tiny-7b"].provenance == "heuristic"
    assert by_id["huge-120b"].tier == "large"


async def test_override_wins_over_heuristic() -> None:
    store = _MemoryStore()
    service = TierAssignmentService(store=store, clock=FakeClock())

    await service.set_override(
        provider="local-host",
        model_id="tiny-7b",
        tier="large",
        provenance="operator",
        reason="operator knows it punches above its size",
    )
    assignments = await service.effective_assignments(_providers())
    tiny = next(a for a in assignments if a.model_id == "tiny-7b")
    assert tiny.tier == "large"
    assert tiny.provenance == "operator"
    assert tiny.confidence == 1.0


async def test_set_override_replaces_prior_entry() -> None:
    store = _MemoryStore()
    service = TierAssignmentService(store=store, clock=FakeClock())

    await service.set_override(
        provider="local-host",
        model_id="tiny-7b",
        tier="medium",
        provenance="llm",
        reason="first offer",
    )
    await service.set_override(
        provider="local-host",
        model_id="tiny-7b",
        tier="large",
        provenance="operator",
        reason="operator correction",
    )
    stored = await store.load()
    assert len(stored.overrides) == 1
    assert stored.overrides[0].tier == "large"
    assert stored.overrides[0].provenance == "operator"


async def test_clear_override_reverts_to_heuristic() -> None:
    store = _MemoryStore()
    service = TierAssignmentService(store=store, clock=FakeClock())
    await service.set_override(
        provider="local-host",
        model_id="tiny-7b",
        tier="large",
        provenance="operator",
        reason="temporary",
    )

    assert await service.clear_override(provider="local-host", model_id="tiny-7b")
    assert not await service.clear_override(provider="local-host", model_id="tiny-7b")

    assignments = await service.effective_assignments(_providers())
    tiny = next(a for a in assignments if a.model_id == "tiny-7b")
    assert tiny.tier == "small"
    assert tiny.provenance == "heuristic"


async def test_tier_lookup_keys_by_provider_and_model() -> None:
    service = TierAssignmentService(store=_MemoryStore(), clock=FakeClock())
    lookup = await service.tier_lookup(_providers())
    assert lookup[("local-host", "tiny-7b")] == "small"
    assert lookup[("local-host", "huge-120b")] == "large"
