"""Tests for the non-destructive ``flag_models_stale`` mutation."""

from datetime import UTC, datetime

import pytest

from synthorg.config.schema import ProviderModelConfig
from synthorg.providers.errors import ProviderNotFoundError
from synthorg.providers.management.service import ProviderManagementService
from tests.unit.providers.management.conftest import make_create_request

pytestmark = pytest.mark.unit

_FLAGGED = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


async def _seed(service: ProviderManagementService, *model_ids: str) -> None:
    await service.create_provider(
        make_create_request(
            models=tuple(ProviderModelConfig(id=m) for m in model_ids),
        ),
    )


class TestFlagModelsStale:
    async def test_flags_matching_models_without_deleting(
        self,
        service: ProviderManagementService,
    ) -> None:
        await _seed(service, "m1", "m2", "m3")
        updated = await service.flag_models_stale(
            "test-provider",
            stale_ids=["m2"],
            reason="removed_from_catalog",
            flagged_at=_FLAGGED,
        )
        by_id = {m.id: m for m in updated.models}
        assert set(by_id) == {"m1", "m2", "m3"}
        assert by_id["m2"].stale is not None
        assert by_id["m2"].stale.reason == "removed_from_catalog"
        assert by_id["m1"].stale is None
        assert by_id["m3"].stale is None

    async def test_persists_flag(
        self,
        service: ProviderManagementService,
    ) -> None:
        await _seed(service, "m1")
        await service.flag_models_stale(
            "test-provider",
            stale_ids=["m1"],
            reason="deprecated",
            flagged_at=_FLAGGED,
            successor_model_id="m2",
        )
        providers = await service.list_providers()
        model = providers["test-provider"].models[0]
        assert model.stale is not None
        assert model.stale.successor_model_id == "m2"

    async def test_idempotent_same_reason_is_noop(
        self,
        service: ProviderManagementService,
    ) -> None:
        await _seed(service, "m1")
        first = await service.flag_models_stale(
            "test-provider",
            stale_ids=["m1"],
            reason="deprecated",
            flagged_at=_FLAGGED,
        )
        again = await service.flag_models_stale(
            "test-provider",
            stale_ids=["m1"],
            reason="deprecated",
            flagged_at=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        )
        assert first.models[0].stale is not None
        assert again.models[0].stale is not None
        # flagged_at did not churn on the second, same-reason call.
        assert again.models[0].stale.flagged_at == _FLAGGED

    async def test_no_matching_ids_returns_unchanged(
        self,
        service: ProviderManagementService,
    ) -> None:
        await _seed(service, "m1")
        updated = await service.flag_models_stale(
            "test-provider",
            stale_ids=["absent"],
            reason="removed_from_catalog",
            flagged_at=_FLAGGED,
        )
        assert updated.models[0].stale is None

    async def test_unknown_provider_raises(
        self,
        service: ProviderManagementService,
    ) -> None:
        with pytest.raises(ProviderNotFoundError):
            await service.flag_models_stale(
                "nonexistent",
                stale_ids=["m1"],
                reason="deprecated",
                flagged_at=_FLAGGED,
            )
