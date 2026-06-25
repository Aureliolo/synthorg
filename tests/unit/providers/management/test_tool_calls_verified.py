"""Tests for the runtime tool-call ``tool_calls_verified`` capability writes."""

import pytest

from synthorg.config.schema import ProviderModelConfig
from synthorg.providers.errors import (
    ProviderModelNotFoundError,
    ProviderNotFoundError,
)
from synthorg.providers.management.service import ProviderManagementService
from tests.unit.providers.management.conftest import make_create_request

pytestmark = pytest.mark.unit


async def _seed(service: ProviderManagementService, *model_ids: str) -> None:
    await service.create_provider(
        make_create_request(
            models=tuple(ProviderModelConfig(id=m) for m in model_ids),
        ),
    )


async def _flag(service: ProviderManagementService, model: str) -> bool | None:
    providers = await service.list_providers()
    by_id = {m.id: m for m in providers["test-provider"].models}
    return by_id[model].metadata.tool_calls_verified


class TestToolCallsVerifiedWrites:
    async def test_mark_unverified_persists_false(
        self, service: ProviderManagementService
    ) -> None:
        await _seed(service, "m1")
        await service.mark_tool_calls_unverified("test-provider", "m1")
        assert await _flag(service, "m1") is False

    async def test_mark_verified_persists_true(
        self, service: ProviderManagementService
    ) -> None:
        await _seed(service, "m1")
        await service.mark_tool_calls_unverified("test-provider", "m1")
        await service.mark_tool_calls_verified("test-provider", "m1")
        assert await _flag(service, "m1") is True

    async def test_clear_resets_to_none(
        self, service: ProviderManagementService
    ) -> None:
        await _seed(service, "m1")
        await service.mark_tool_calls_unverified("test-provider", "m1")
        await service.clear_tool_calls_verification("test-provider", "m1")
        assert await _flag(service, "m1") is None

    async def test_mark_verified_is_noop_on_untested_model(
        self, service: ProviderManagementService
    ) -> None:
        # A success on a never-downgraded (None) model must NOT promote it to
        # True: optimism already selects it, so the runtime proof is not worth
        # a provider-config rewrite + registry hot-reload.
        await _seed(service, "m1")
        changed = await service.mark_tool_calls_verified("test-provider", "m1")
        assert changed is False
        assert await _flag(service, "m1") is None

    async def test_writes_report_changed_via_return(
        self, service: ProviderManagementService
    ) -> None:
        await _seed(service, "m1")
        unverify = await service.mark_tool_calls_unverified("test-provider", "m1")
        assert unverify is True
        # Idempotent second call does not rewrite.
        unverify_again = await service.mark_tool_calls_unverified("test-provider", "m1")
        assert unverify_again is False
        verify = await service.mark_tool_calls_verified("test-provider", "m1")
        assert verify is True
        cleared = await service.clear_tool_calls_verification("test-provider", "m1")
        assert cleared is True
        cleared_again = await service.clear_tool_calls_verification(
            "test-provider", "m1"
        )
        assert cleared_again is False

    async def test_only_named_model_affected(
        self, service: ProviderManagementService
    ) -> None:
        await _seed(service, "m1", "m2")
        await service.mark_tool_calls_unverified("test-provider", "m1")
        assert await _flag(service, "m1") is False
        assert await _flag(service, "m2") is None

    async def test_unknown_provider_raises(
        self, service: ProviderManagementService
    ) -> None:
        with pytest.raises(ProviderNotFoundError):
            await service.mark_tool_calls_unverified("nonexistent", "m1")

    async def test_unknown_model_raises(
        self, service: ProviderManagementService
    ) -> None:
        await _seed(service, "m1")
        with pytest.raises(ProviderModelNotFoundError):
            await service.mark_tool_calls_unverified("test-provider", "absent")
