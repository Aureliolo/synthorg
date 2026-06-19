"""Unit tests for the new ``ProviderManagementService`` mutations.

Covers rate-limits update, credentials rotation, manual model add,
and bulk model sync.  Each test exercises both the service-layer
state transition and the audit-row emission.
"""

from datetime import UTC, datetime
from typing import override
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api.dto_provider_capabilities import (
    AddModelRequest,
    PresetOverride,
    PresetOverrideUpdateRequest,
    ProviderAuditActor,
    ProviderAuditEvent,
    RateLimitsUpdateRequest,
    SyncModelsRequest,
    _ApiKeyRotation,
    _CustomHeaderRotation,
    _OAuthRotation,
    _SubscriptionRotation,
)
from synthorg.api.state import AppState
from synthorg.config.schema import ProviderConfig, ProviderModelConfig, RootConfig
from synthorg.core.actor_context import ActorIdentity, ActorKind, actor_scope
from synthorg.core.domain_errors import ConflictError
from synthorg.core.resilience_config import RateLimiterConfig
from synthorg.persistence.provider_audit_protocol import ProviderAuditFilterSpec
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
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from tests._shared import make_in_memory_catalog

pytestmark = pytest.mark.unit


class _FakeAuditRepo:
    def __init__(self) -> None:
        self.records: list[ProviderAuditEvent] = []
        self._next_id = 1

    async def record(self, event: ProviderAuditEvent) -> ProviderAuditEvent:
        saved = event.model_copy(update={"id": self._next_id})
        self._next_id += 1
        self.records.append(saved)
        return saved

    async def append(self, event: ProviderAuditEvent) -> None:
        await self.record(event)

    async def list(
        self,
        *,
        provider_name: str,
        after_id: int | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ProviderAuditEvent, ...], bool]:
        return ((), False)

    async def query(
        self,
        filter_spec: ProviderAuditFilterSpec,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- canonical ADR-0001 page size
        offset: int = 0,
    ) -> tuple[ProviderAuditEvent, ...]:
        return ()

    async def purge_before(self, threshold: datetime) -> int:
        return 0

    async def purge_before_id(self, *, before_id: int) -> int:
        return 0


class _FakeOverrideRepo:
    def __init__(self) -> None:
        self.store: dict[str, PresetOverride] = {}

    async def get(self, preset_name: str) -> PresetOverride | None:
        return self.store.get(preset_name)

    async def save(self, override: PresetOverride) -> None:
        self.store[override.preset_name] = override

    async def save_if_unchanged(
        self,
        override: PresetOverride,
        /,
        *,
        expected_updated_at: datetime | None,
    ) -> bool:
        existing = self.store.get(override.preset_name)
        observed = existing.updated_at if existing is not None else None
        if observed != expected_updated_at:
            return False
        self.store[override.preset_name] = override
        return True

    async def delete(self, preset_name: str) -> bool:
        return self.store.pop(preset_name, None) is not None

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- canonical ADR-0001 page size
        offset: int = 0,
    ) -> tuple[PresetOverride, ...]:
        items = sorted(self.store.values(), key=lambda o: o.preset_name)
        return tuple(items[offset : offset + limit])


def _make_provider_config(
    name: str = "cloud-test",
    *,
    models: tuple[ProviderModelConfig, ...] = (),
    rate_limiter: RateLimiterConfig | None = None,
    auth_type: AuthType = AuthType.API_KEY,
) -> ProviderConfig:
    extras: dict[str, object] = {}
    if auth_type == AuthType.API_KEY:
        # Catalog-only credentials: the secret lives in the connection
        # catalog (pre-minted by the service fixture), referenced here.
        extras["connection_name"] = f"provider-{name}"
    elif auth_type == AuthType.SUBSCRIPTION:
        extras["subscription_token"] = "initial-token-x"
        extras["tos_accepted_at"] = datetime.now(UTC).isoformat()
    elif auth_type == AuthType.CUSTOM_HEADER:
        extras["custom_header_name"] = "X-Init-Token"
        extras["custom_header_value"] = "initial-header-x"
    elif auth_type == AuthType.OAUTH:
        extras["oauth_token_url"] = "https://oauth.example.com/token"
        extras["oauth_client_id"] = "init-client-id"
        extras["oauth_client_secret"] = "initial-oauth-secret-x"
        extras["oauth_scope"] = None
    return ProviderConfig(
        driver="litellm",
        litellm_provider="cloud-test",
        auth_type=auth_type,
        base_url=None,
        models=models,
        rate_limiter=rate_limiter or RateLimiterConfig(),
        preset_name=None,
        **extras,  # type: ignore[arg-type]
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
    settings_service = AsyncMock(spec=SettingsService)
    config_resolver = AsyncMock(spec=ConfigResolver)
    app_state = MagicMock(spec=AppState)
    app_state.swap_provider_registry = MagicMock()
    app_state.swap_model_router = MagicMock()
    # Functional in-memory credential catalog so the catalog-only
    # rotation path (mint secret, re-point connection_name) round-trips.
    app_state.slice.return_value.provider_credential_catalog = make_in_memory_catalog()
    config = MagicMock(spec=RootConfig)
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
        )
        assert len(audit_repo.records) == 1
        assert audit_repo.records[0].event_type == "provider_rate_limits_updated"

    async def test_audit_actor_resolved_from_context(
        self,
        service: ProviderManagementService,
        audit_repo: _FakeAuditRepo,
    ) -> None:
        """A bound HUMAN actor reaches the audit row without threading.

        The mutation method no longer accepts an ``actor`` argument; the
        audit leaf resolves it from the ``actor_context`` seam that
        ``AuthContextMiddleware`` binds on every authenticated request.
        """
        bound = ActorIdentity(
            actor_id="user-1",
            kind=ActorKind.HUMAN,
            label="Operator",
        )
        with actor_scope(bound):
            await service.update_rate_limits(
                "cloud-test",
                RateLimitsUpdateRequest(requests_per_minute=42),
            )
        assert len(audit_repo.records) == 1
        recorded_actor = audit_repo.records[0].actor
        assert recorded_actor.id == "user-1"
        assert recorded_actor.label == "Operator"

    async def test_audit_actor_falls_back_to_system_without_binding(
        self,
        service: ProviderManagementService,
        audit_repo: _FakeAuditRepo,
    ) -> None:
        """No bound actor (background path) attributes the system sentinel."""
        await service.update_rate_limits(
            "cloud-test",
            RateLimitsUpdateRequest(requests_per_minute=24),
        )
        assert len(audit_repo.records) == 1
        assert audit_repo.records[0].actor.id == "system"


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
        )
        # Catalog-only credentials: the rotated secret resolves from the
        # connection catalog via the config's connection_name, never an
        # embedded field.
        assert result.connection_name == "provider-cloud-test"
        assert await service._resolve_provider_api_key(result) == "rotated-secret-y"
        # Round-trip the config to confirm rotation persisted: the
        # in-memory provider state reflects the new key, not the old.
        persisted = await service.get_provider("cloud-test")
        assert await service._resolve_provider_api_key(persisted) == "rotated-secret-y"
        # Audit row carries the masked secret only.
        assert len(audit_repo.records) == 1
        masked = audit_repo.records[0].payload["masked_secret"]
        assert isinstance(masked, str)
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
            )


@pytest.mark.unit
class TestRotateCredentialsAllAuthTypes:
    """Cover the SUBSCRIPTION / CUSTOM_HEADER / OAUTH branches of
    ``credentials_update_fields`` end-to-end through the service."""

    @staticmethod
    def _build_service(
        audit_service: ProviderAuditService,
        provider: ProviderConfig,
    ) -> ProviderManagementService:
        settings_service = AsyncMock(spec=SettingsService)
        config_resolver = AsyncMock(spec=ConfigResolver)
        app_state = MagicMock(spec=AppState)
        app_state.swap_provider_registry = MagicMock()
        app_state.swap_model_router = MagicMock()
        config = MagicMock(spec=RootConfig)
        config.providers = {}
        initial = {"cloud-test": provider}
        config_resolver.get_provider_configs = AsyncMock(return_value=initial)
        svc = ProviderManagementService(
            settings_service=settings_service,
            config_resolver=config_resolver,
            app_state=app_state,
            config=config,
            audit_service=audit_service,
        )

        def _persist_stub(new_providers: dict[str, ProviderConfig]) -> None:
            config_resolver.get_provider_configs = AsyncMock(return_value=new_providers)

        svc._validate_and_persist = AsyncMock(  # type: ignore[method-assign]
            side_effect=_persist_stub,
        )
        return svc

    async def test_rotate_subscription_token(
        self,
        actor: ProviderAuditActor,
    ) -> None:
        audit_repo = _FakeAuditRepo()
        audit = ProviderAuditService(audit_repo)
        provider = _make_provider_config(auth_type=AuthType.SUBSCRIPTION)
        service = self._build_service(audit, provider)

        request = _SubscriptionRotation.model_validate(
            {
                "auth_type": AuthType.SUBSCRIPTION,
                "subscription_token": "rotated-sub-token-y",
                "tos_accepted": True,
            },
        )
        result = await service.rotate_credentials(
            "cloud-test",
            request,
        )
        assert result.subscription_token == "rotated-sub-token-y"
        assert result.tos_accepted_at is not None
        assert len(audit_repo.records) == 1
        masked = audit_repo.records[0].payload["masked_secret"]
        assert isinstance(masked, str)
        assert "rota" in masked
        assert "en-y" in masked
        assert "rotated-sub-token-y" not in masked

    async def test_rotate_custom_header(self, actor: ProviderAuditActor) -> None:
        audit_repo = _FakeAuditRepo()
        audit = ProviderAuditService(audit_repo)
        provider = _make_provider_config(auth_type=AuthType.CUSTOM_HEADER)
        service = self._build_service(audit, provider)

        request = _CustomHeaderRotation.model_validate(
            {
                "auth_type": AuthType.CUSTOM_HEADER,
                "custom_header_name": "X-Rotated-Token",
                "custom_header_value": "rotated-header-zzz",
            },
        )
        result = await service.rotate_credentials(
            "cloud-test",
            request,
        )
        assert result.custom_header_name == "X-Rotated-Token"
        assert result.custom_header_value == "rotated-header-zzz"
        assert len(audit_repo.records) == 1
        masked = audit_repo.records[0].payload["masked_secret"]
        assert isinstance(masked, str)
        assert "rota" in masked
        assert "-zzz" in masked
        assert "rotated-header-zzz" not in masked

    async def test_rotate_oauth_credentials(
        self,
        actor: ProviderAuditActor,
    ) -> None:
        audit_repo = _FakeAuditRepo()
        audit = ProviderAuditService(audit_repo)
        provider = _make_provider_config(auth_type=AuthType.OAUTH)
        service = self._build_service(audit, provider)

        request = _OAuthRotation.model_validate(
            {
                "auth_type": AuthType.OAUTH,
                "oauth_token_url": "https://oauth.example.com/token2",
                "oauth_client_id": "client-id-rotated",
                "oauth_client_secret": "rotated-oauth-secret-yyy",
                "oauth_scope": "read write",
            },
        )
        result = await service.rotate_credentials(
            "cloud-test",
            request,
        )
        assert result.oauth_client_secret == "rotated-oauth-secret-yyy"
        assert result.oauth_client_id == "client-id-rotated"
        assert result.oauth_scope == "read write"
        assert len(audit_repo.records) == 1
        masked = audit_repo.records[0].payload["masked_secret"]
        assert isinstance(masked, str)
        assert "rota" in masked
        assert "-yyy" in masked
        assert "rotated-oauth-secret-yyy" not in masked


@pytest.mark.unit
class TestSyncModels:
    """Cover both ``replace_existing`` branches of ``sync_models``."""

    async def test_sync_replace_existing_appends_and_removes(
        self,
        service: ProviderManagementService,
        audit_repo: _FakeAuditRepo,
        actor: ProviderAuditActor,
    ) -> None:
        # Seed with one model that will be removed by the sync.
        old_model = ProviderModelConfig(id="old-model-001", alias="old")
        config = _make_provider_config(models=(old_model,))
        service._config_resolver.get_provider_configs = AsyncMock(  # type: ignore[method-assign]
            return_value={"cloud-test": config},
        )

        # Stub discovery to return a fresh set with one new model.
        new_model = ProviderModelConfig(id="new-model-001", alias="new")
        service.discover_models_for_provider = AsyncMock(  # type: ignore[method-assign]
            return_value=(new_model,),
        )

        result = await service.sync_models(
            "cloud-test",
            SyncModelsRequest(replace_existing=True),
        )
        assert result.added == ("new-model-001",)
        assert result.removed == ("old-model-001",)
        assert len(audit_repo.records) == 1
        assert audit_repo.records[0].event_type == "models_synced"
        payload = audit_repo.records[0].payload
        assert payload["added_count"] == 1
        assert payload["removed_count"] == 1

    async def test_sync_append_only_keeps_existing(
        self,
        service: ProviderManagementService,
        actor: ProviderAuditActor,
    ) -> None:
        old_model = ProviderModelConfig(id="keep-model-001", alias="keep")
        config = _make_provider_config(models=(old_model,))
        service._config_resolver.get_provider_configs = AsyncMock(  # type: ignore[method-assign]
            return_value={"cloud-test": config},
        )
        added_model = ProviderModelConfig(id="added-model-001", alias="added")
        service.discover_models_for_provider = AsyncMock(  # type: ignore[method-assign]
            return_value=(added_model,),
        )

        result = await service.sync_models(
            "cloud-test",
            SyncModelsRequest(replace_existing=False),
        )
        assert result.added == ("added-model-001",)
        assert result.removed == ()
        # Existing model is preserved.
        assert any(m.id == "keep-model-001" for m in result.models)

    async def test_sync_rejects_when_provider_endpoint_changed_during_discovery(
        self,
        service: ProviderManagementService,
        actor: ProviderAuditActor,
    ) -> None:
        """Don't persist discovered models onto a swapped endpoint.

        If the provider's ``base_url`` (or ``auth_type`` /
        ``preset_name``) is mutated while discovery is in flight,
        the resulting model set came from a different upstream and
        must not be persisted onto the new config.
        """
        original = _make_provider_config(
            models=(ProviderModelConfig(id="m1", alias="m1"),),
        )
        # First call returns ``original``; second call (post-discovery)
        # returns a config with a different ``base_url`` to simulate a
        # concurrent endpoint swap.
        swapped = original.model_copy(
            update={"base_url": "https://swapped.example.com/v1"},
        )
        service._config_resolver.get_provider_configs = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                {"cloud-test": original},  # get_provider() pre-discover
                {"cloud-test": swapped},  # post-lock snapshot
            ],
        )
        service.discover_models_for_provider = AsyncMock(  # type: ignore[method-assign]
            return_value=(ProviderModelConfig(id="m2", alias="m2"),),
        )
        with pytest.raises(ProviderValidationError, match="configuration changed"):
            await service.sync_models(
                "cloud-test",
                SyncModelsRequest(replace_existing=True),
            )

    async def test_sync_rejects_when_models_added_between_pre_discover_and_lock(
        self,
        service: ProviderManagementService,
        actor: ProviderAuditActor,
    ) -> None:
        """Race: pre-lock snapshot empty, post-lock snapshot non-empty.

        If a concurrent ``add_model()`` lands between the pre-discover
        read and lock acquisition, ``replace_existing=True`` with an
        empty discovery would wipe the freshly-added models unless the
        guard re-checks against the post-lock snapshot.
        """
        empty = _make_provider_config(models=())
        # Concurrent ``add_model()`` populated the persisted set
        # between the pre-discover read and lock acquisition.
        populated = empty.model_copy(
            update={"models": (ProviderModelConfig(id="race-m1", alias="m1"),)},
        )
        service._config_resolver.get_provider_configs = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                {"cloud-test": empty},  # get_provider() pre-discover
                {"cloud-test": populated},  # post-lock snapshot
            ],
        )
        # Discovery returns empty (the destructive trigger).
        service.discover_models_for_provider = AsyncMock(  # type: ignore[method-assign]
            return_value=(),
        )
        with pytest.raises(ProviderValidationError, match="refusing destructive"):
            await service.sync_models(
                "cloud-test",
                SyncModelsRequest(replace_existing=True),
            )


@pytest.mark.unit
class TestSubscriptionRotationToSGuard:
    """Subscription rotation rejects ``tos_accepted=false``."""

    async def test_subscription_rotation_with_tos_false_rejected(
        self,
        actor: ProviderAuditActor,
    ) -> None:
        from synthorg.api.dto_provider_capabilities import _SubscriptionRotation

        audit_repo = _FakeAuditRepo()
        audit = ProviderAuditService(audit_repo)
        provider = _make_provider_config(auth_type=AuthType.SUBSCRIPTION)
        service = TestRotateCredentialsAllAuthTypes._build_service(
            audit,
            provider,
        )

        request = _SubscriptionRotation.model_validate(
            {
                "auth_type": AuthType.SUBSCRIPTION,
                "subscription_token": "rotated-sub-token-y",
                "tos_accepted": False,
            },
        )
        with pytest.raises(ProviderValidationError, match="tos_accepted=true"):
            await service.rotate_credentials(
                "cloud-test",
                request,
            )
        # Audit row must NOT be written when validation rejects.
        assert len(audit_repo.records) == 0


@pytest.mark.unit
class TestAuditFailureIsolation:
    """The mutation succeeds even if the audit write fails."""

    async def test_audit_repo_raises_does_not_break_mutation(
        self,
        actor: ProviderAuditActor,
    ) -> None:
        # Fake audit repo whose record() always raises.
        class _ExplodingRepo:
            async def record(
                self,
                event: ProviderAuditEvent,
            ) -> ProviderAuditEvent:
                msg = "audit backend down"
                raise RuntimeError(msg)

            async def append(self, event: ProviderAuditEvent) -> None:
                await self.record(event)

            async def list(
                self,
                *,
                provider_name: str,
                after_id: int | None = None,
                limit: int = 50,
            ) -> tuple[tuple[ProviderAuditEvent, ...], bool]:
                return (), False

            async def query(
                self,
                filter_spec: ProviderAuditFilterSpec,
                *,
                limit: int = 100,  # lint-allow: magic-numbers -- ADR-0001
                offset: int = 0,
            ) -> tuple[ProviderAuditEvent, ...]:
                return ()

            async def purge_before(self, threshold: datetime) -> int:
                return 0

            async def purge_before_id(
                self,
                *,
                before_id: int,
            ) -> int:
                return 0

        audit_service = ProviderAuditService(_ExplodingRepo())
        provider = _make_provider_config()
        svc = TestRotateCredentialsAllAuthTypes._build_service(
            audit_service,
            provider,
        )
        # The mutation must succeed even though the audit write blew up.
        result = await svc.update_rate_limits(
            "cloud-test",
            RateLimitsUpdateRequest(requests_per_minute=99),
        )
        assert result.requests_per_minute == 99


@pytest.fixture
def stub_preset_lookup(monkeypatch: pytest.MonkeyPatch) -> str:
    """Make the preset catalog return a vendor-agnostic preset.

    ``PresetOverrideService.upsert_override`` rejects unknown
    preset names by calling ``get_preset(name)``; tests here would
    otherwise need to use real vendor names like ``"openai"``.
    Patching the symbol the service imports lets us assert the
    happy-path with a fixture-owned name.
    """
    from synthorg.providers.presets import CloudPreset

    fake_name = "test-cloud-provider"
    fake_preset = CloudPreset(
        name=fake_name,
        display_name="Test Cloud Provider",
        description="Vendor-agnostic preset used in unit tests",
        driver="litellm",
        litellm_provider="test-cloud-provider",
        auth_type=AuthType.API_KEY,
        supported_auth_types=(AuthType.API_KEY,),
        default_base_url="https://api.example.com/v1",
        requires_base_url=False,
        default_models=(),
        is_featured=False,
    )

    def _fake_get_preset(name: str) -> CloudPreset | None:
        return fake_preset if name == fake_name else None

    monkeypatch.setattr(
        "synthorg.providers.management.preset_override_service.get_preset",
        _fake_get_preset,
    )
    return fake_name


@pytest.mark.unit
class TestPresetOverrideService:
    async def test_upsert_then_get(
        self,
        actor: ProviderAuditActor,
        stub_preset_lookup: str,
    ) -> None:
        repo = _FakeOverrideRepo()
        audit = ProviderAuditService(_FakeAuditRepo())
        service = PresetOverrideService(repo, audit_service=audit)
        await service.upsert_override(
            stub_preset_lookup,
            PresetOverrideUpdateRequest(
                base_url="https://api.example.com/v1",
            ),
            actor=actor,
        )
        loaded = await service.get_override(stub_preset_lookup)
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

    async def test_upsert_audit_row(
        self,
        actor: ProviderAuditActor,
        stub_preset_lookup: str,
    ) -> None:
        repo = _FakeOverrideRepo()
        audit_repo = _FakeAuditRepo()
        audit = ProviderAuditService(audit_repo)
        service = PresetOverrideService(repo, audit_service=audit)
        await service.upsert_override(
            stub_preset_lookup,
            PresetOverrideUpdateRequest(base_url="https://api.example.com"),
            actor=actor,
        )
        assert len(audit_repo.records) == 1
        assert audit_repo.records[0].event_type == "preset_override_updated"

    async def test_delete_idempotent(self, actor: ProviderAuditActor) -> None:
        repo = _FakeOverrideRepo()
        service = PresetOverrideService(repo)
        # Delete with no row present.
        result = await service.delete_override("test-cloud-provider", actor=actor)
        assert result is False

    async def test_upsert_lost_race_raises_conflict(
        self,
        actor: ProviderAuditActor,
        stub_preset_lookup: str,
    ) -> None:
        # A concurrent writer shifts the stored row's ``updated_at``
        # between this caller's read and conditional write: the CAS
        # must fail rather than clobber the winner.
        class _RacingRepo(_FakeOverrideRepo):
            @override
            async def save_if_unchanged(
                self,
                override: PresetOverride,
                *,
                expected_updated_at: datetime | None,
            ) -> bool:
                return False

        service = PresetOverrideService(_RacingRepo())
        with pytest.raises(ConflictError, match="modified concurrently"):
            await service.upsert_override(
                stub_preset_lookup,
                PresetOverrideUpdateRequest(base_url="https://api.example.com/v1"),
                actor=actor,
            )


@pytest.mark.unit
class TestMaskSecret:
    """Direct unit tests for the credential-masking helper."""

    def test_mask_secret_long(self) -> None:
        from synthorg.providers.management._capability_helpers import mask_secret

        # 16-char secret: first 4 + *** + last 4 = "abcd***mnop".
        masked = mask_secret("abcdefghijklmnop")
        assert masked == "abcd***mnop"
        assert "efgh" not in masked  # middle chars never leak

    def test_mask_secret_exactly_eight_fully_masked(self) -> None:
        from synthorg.providers.management._capability_helpers import mask_secret

        # Boundary: at exactly 8 chars, the first-4 and last-4
        # windows together cover every byte of the secret, so
        # partial masking would in fact reveal the whole value.
        # Therefore 8-char inputs MUST mask entirely.
        masked = mask_secret("abcdwxyz")
        assert masked == "********"
        assert "abcd" not in masked
        assert "wxyz" not in masked

    def test_mask_secret_short_fully_masked(self) -> None:
        from synthorg.providers.management._capability_helpers import mask_secret

        # 7 chars (<= 8 threshold) -> entirely masked, never reveals
        # any prefix/suffix that would together expose the value.
        masked = mask_secret("abc1234")
        assert masked == "********"
        assert "abc" not in masked
        assert "1234" not in masked
        assert "234" not in masked

    def test_mask_secret_single_char(self) -> None:
        from synthorg.providers.management._capability_helpers import mask_secret

        assert mask_secret("x") == "********"

    def test_mask_secret_empty(self) -> None:
        from synthorg.providers.management._capability_helpers import mask_secret

        assert mask_secret("") == "********"
