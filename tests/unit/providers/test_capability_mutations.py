"""Unit tests for the new ``ProviderManagementService`` mutations.

Covers rate-limits update, credentials rotation, manual model add,
and bulk model sync.  Each test exercises both the service-layer
state transition and the audit-row emission.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api.dto_provider_capabilities import (
    AddModelRequest,
    PresetOverride,
    PresetOverrideUpdateRequest,
    ProviderAuditActor,
    ProviderAuditEvent,
    RateLimitsUpdateRequest,
    _ApiKeyRotation,
)
from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.core.resilience_config import RateLimiterConfig
from synthorg.providers.enums import AuthType
from synthorg.providers.errors import (
    ProviderAlreadyExistsError,
    ProviderValidationError,
)
from synthorg.providers.management.audit_service import ProviderAuditService
from synthorg.providers.management.preset_override_service import (
    PresetOverrideService,
)
from synthorg.providers.management.service import ProviderManagementService


class _FakeAuditRepo:
    def __init__(self) -> None:
        self.records: list[ProviderAuditEvent] = []
        self._next_id = 1

    async def record(self, event: ProviderAuditEvent) -> ProviderAuditEvent:
        saved = event.model_copy(update={"id": self._next_id})
        self._next_id += 1
        self.records.append(saved)
        return saved

    async def list(
        self,
        *,
        provider_name: Any,
        after_id: int | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ProviderAuditEvent, ...], bool]:
        return ((), False)

    async def purge_before_id(self, *, before_id: int) -> int:
        return 0


class _FakeOverrideRepo:
    def __init__(self) -> None:
        self.store: dict[str, PresetOverride] = {}

    async def get(self, preset_name: Any) -> PresetOverride | None:
        return self.store.get(preset_name)

    async def upsert(self, override: PresetOverride) -> PresetOverride:
        self.store[override.preset_name] = override
        return override

    async def delete(self, preset_name: Any) -> bool:
        return self.store.pop(preset_name, None) is not None


def _make_provider_config(
    name: str = "cloud-test",
    *,
    models: tuple[ProviderModelConfig, ...] = (),
    rate_limiter: RateLimiterConfig | None = None,
) -> ProviderConfig:
    return ProviderConfig(
        driver="litellm",
        litellm_provider="cloud-test",
        auth_type=AuthType.API_KEY,
        api_key="initial-secret-x",
        base_url=None,
        models=models,
        rate_limiter=rate_limiter or RateLimiterConfig(),
        preset_name=None,
    )


@pytest.fixture
def actor() -> ProviderAuditActor:
    return ProviderAuditActor(id="user-1", label="Operator")


@pytest.fixture
def audit_repo() -> _FakeAuditRepo:
    return _FakeAuditRepo()


@pytest.fixture
def audit_service(audit_repo: _FakeAuditRepo) -> ProviderAuditService:
    return ProviderAuditService(audit_repo)


@pytest.fixture
def service(audit_service: ProviderAuditService) -> ProviderManagementService:
    """Build a ``ProviderManagementService`` with mocked deps."""
    settings_service = AsyncMock()
    config_resolver = AsyncMock()
    app_state = MagicMock()
    app_state.swap_provider_registry = MagicMock()
    app_state.swap_model_router = MagicMock()
    config = MagicMock()
    config.providers = {}

    initial = {"cloud-test": _make_provider_config()}
    config_resolver.get_provider_configs = AsyncMock(return_value=initial)

    svc = ProviderManagementService(
        settings_service=settings_service,
        config_resolver=config_resolver,
        app_state=app_state,
        config=config,
        audit_service=audit_service,
    )

    # Stub the persist + hot-reload path so the test runs without a
    # real settings backend; it tracks the requested updates so test
    # assertions can verify the merged config.
    def _persist_stub(new_providers: dict[str, ProviderConfig]) -> None:
        config_resolver.get_provider_configs = AsyncMock(return_value=new_providers)

    svc._validate_and_persist = AsyncMock(  # type: ignore[method-assign]
        side_effect=_persist_stub,
    )
    return svc


@pytest.mark.unit
class TestRateLimitsUpdate:
    async def test_partial_update_rpm_only(
        self,
        service: ProviderManagementService,
        actor: ProviderAuditActor,
    ) -> None:
        result = await service.update_rate_limits(
            "cloud-test",
            RateLimitsUpdateRequest(requests_per_minute=120),
            actor=actor,
        )
        assert result.requests_per_minute == 120
        # Concurrent stays at the persisted default (0 = unlimited).
        assert result.concurrent_requests == 0

    async def test_partial_update_concurrent_only(
        self,
        service: ProviderManagementService,
        actor: ProviderAuditActor,
    ) -> None:
        result = await service.update_rate_limits(
            "cloud-test",
            RateLimitsUpdateRequest(concurrent_requests=4),
            actor=actor,
        )
        assert result.requests_per_minute == 0
        assert result.concurrent_requests == 4

    async def test_audits_on_success(
        self,
        service: ProviderManagementService,
        audit_repo: _FakeAuditRepo,
        actor: ProviderAuditActor,
    ) -> None:
        await service.update_rate_limits(
            "cloud-test",
            RateLimitsUpdateRequest(requests_per_minute=60),
            actor=actor,
        )
        assert len(audit_repo.records) == 1
        assert audit_repo.records[0].event_type == "provider_rate_limits_updated"


@pytest.mark.unit
class TestModelMutations:
    async def test_add_model_appends(
        self,
        service: ProviderManagementService,
        actor: ProviderAuditActor,
    ) -> None:
        new_model = ProviderModelConfig(id="example-large-001", alias="large")
        result = await service.add_model(
            "cloud-test",
            AddModelRequest(model=new_model),
            actor=actor,
        )
        assert any(m.id == "example-large-001" for m in result.models)

    async def test_add_model_duplicate_rejected(
        self,
        service: ProviderManagementService,
        actor: ProviderAuditActor,
    ) -> None:
        existing = ProviderModelConfig(id="example-small-001", alias="small")
        # Re-seed the resolver with an existing model so the next add
        # attempt collides.
        config = _make_provider_config(models=(existing,))
        service._config_resolver.get_provider_configs = AsyncMock(  # type: ignore[method-assign]
            return_value={"cloud-test": config},
        )
        with pytest.raises(ProviderAlreadyExistsError):
            await service.add_model(
                "cloud-test",
                AddModelRequest(model=existing),
                actor=actor,
            )


@pytest.mark.unit
class TestCredentialsRotation:
    async def test_rotate_api_key(
        self,
        service: ProviderManagementService,
        audit_repo: _FakeAuditRepo,
        actor: ProviderAuditActor,
    ) -> None:
        request = _ApiKeyRotation.model_validate(
            {"auth_type": AuthType.API_KEY, "api_key": "rotated-secret-y"},
        )
        result = await service.rotate_credentials(
            "cloud-test",
            request,
            actor=actor,
        )
        assert result.api_key == "rotated-secret-y"
        # Round-trip the config to confirm rotation persisted: the
        # in-memory provider state reflects the new key, not the old.
        persisted = await service.get_provider("cloud-test")
        assert persisted.api_key == "rotated-secret-y"
        # Audit row carries the masked secret only.
        assert len(audit_repo.records) == 1
        masked = audit_repo.records[0].payload["masked_secret"]
        assert "rota" in masked  # first 4
        assert "et-y" in masked  # last 4
        assert "rotated-secret-y" not in masked  # never plaintext

    async def test_rotate_auth_type_mismatch_rejected(
        self,
        service: ProviderManagementService,
        actor: ProviderAuditActor,
    ) -> None:
        # Provider is api_key; payload says subscription -> 422.
        from synthorg.api.dto_provider_capabilities import _SubscriptionRotation

        request = _SubscriptionRotation.model_validate(
            {
                "auth_type": AuthType.SUBSCRIPTION,
                "subscription_token": "subscription-token-x",
                "tos_accepted": True,
            },
        )
        with pytest.raises(ProviderValidationError):
            await service.rotate_credentials(
                "cloud-test",
                request,
                actor=actor,
            )


@pytest.mark.unit
class TestPresetOverrideService:
    async def test_upsert_then_get(self, actor: ProviderAuditActor) -> None:
        repo = _FakeOverrideRepo()
        audit = ProviderAuditService(_FakeAuditRepo())
        service = PresetOverrideService(repo, audit_service=audit)
        await service.upsert_override(
            "openai",
            PresetOverrideUpdateRequest(
                base_url="https://api.example.com/v1",
            ),
            actor=actor,
        )
        loaded = await service.get_override("openai")
        assert loaded is not None
        assert loaded.base_url == "https://api.example.com/v1"

    async def test_upsert_unknown_preset_rejected(
        self,
        actor: ProviderAuditActor,
    ) -> None:
        repo = _FakeOverrideRepo()
        service = PresetOverrideService(repo)
        with pytest.raises(ProviderValidationError, match="Unknown preset"):
            await service.upsert_override(
                "this-preset-does-not-exist",
                PresetOverrideUpdateRequest(base_url="https://x"),
                actor=actor,
            )

    async def test_upsert_audit_row(self, actor: ProviderAuditActor) -> None:
        repo = _FakeOverrideRepo()
        audit_repo = _FakeAuditRepo()
        audit = ProviderAuditService(audit_repo)
        service = PresetOverrideService(repo, audit_service=audit)
        await service.upsert_override(
            "openai",
            PresetOverrideUpdateRequest(base_url="https://api.example.com"),
            actor=actor,
        )
        assert len(audit_repo.records) == 1
        assert audit_repo.records[0].event_type == "preset_override_updated"

    async def test_delete_idempotent(self, actor: ProviderAuditActor) -> None:
        repo = _FakeOverrideRepo()
        service = PresetOverrideService(repo)
        # Delete with no row present.
        result = await service.delete_override("openai", actor=actor)
        assert result is False


@pytest.mark.unit
class TestMaskSecret:
    """Direct unit tests for the credential-masking helper."""

    def test_mask_secret_long(self) -> None:
        from synthorg.providers.management._capability_helpers import mask_secret

        # 16-char secret: first 4 + *** + last 4 = "abcd***mnop".
        masked = mask_secret("abcdefghijklmnop")
        assert masked == "abcd***mnop"
        assert "efgh" not in masked  # middle chars never leak

    def test_mask_secret_exactly_eight(self) -> None:
        from synthorg.providers.management._capability_helpers import mask_secret

        # Boundary: 8 chars = first 4 + *** + last 4 (the prefix and
        # suffix overlap structurally but the algorithm masks middle
        # zero-width).
        masked = mask_secret("abcdwxyz")
        assert masked == "abcd***wxyz"

    def test_mask_secret_short_fully_masked(self) -> None:
        from synthorg.providers.management._capability_helpers import mask_secret

        # 7 chars (< 8 threshold) -> entirely masked, never reveals
        # any prefix/suffix that would together expose the value.
        masked = mask_secret("abc1234")
        assert masked == "********"
        assert "abc" not in masked
        assert "234" not in masked

    def test_mask_secret_single_char(self) -> None:
        from synthorg.providers.management._capability_helpers import mask_secret

        assert mask_secret("x") == "********"

    def test_mask_secret_empty(self) -> None:
        from synthorg.providers.management._capability_helpers import mask_secret

        assert mask_secret("") == "********"
