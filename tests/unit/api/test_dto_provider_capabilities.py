"""Tests for the post-CRUD provider capability DTOs."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.api.dto_provider_capabilities import (
    AddModelRequest,
    PresetOverride,
    PresetOverrideUpdateRequest,
    ProviderAuditActor,
    ProviderAuditEvent,
    RateLimitsResponse,
    RateLimitsUpdateRequest,
    SyncModelsRequest,
    SyncModelsResponse,
)
from synthorg.config.schema import ProviderModelConfig
from synthorg.providers.enums import AuthType


@pytest.mark.unit
class TestProviderAuditEvent:
    def test_full_construction(self) -> None:
        actor = ProviderAuditActor(id="user-1", label="Operator")
        event = ProviderAuditEvent(
            id=1,
            provider_name="cloud-test",
            event_type="provider_created",
            actor=actor,
            payload={"driver": "litellm"},
            occurred_at=datetime.now(UTC),
        )
        assert event.id == 1
        assert event.event_type == "provider_created"
        assert event.actor.label == "Operator"
        assert event.payload == {"driver": "litellm"}

    def test_naive_datetime_rejected(self) -> None:
        actor = ProviderAuditActor(id="user-1", label="Operator")
        with pytest.raises(ValidationError):
            ProviderAuditEvent(
                provider_name="cloud-test",
                event_type="provider_created",
                actor=actor,
                payload={},
                occurred_at=datetime(2026, 4, 28, 0, 0, 0),  # noqa: DTZ001
            )

    def test_blank_provider_name_rejected(self) -> None:
        actor = ProviderAuditActor(id="user-1", label="Operator")
        with pytest.raises(ValidationError):
            ProviderAuditEvent(
                provider_name="",
                event_type="provider_created",
                actor=actor,
                payload={},
                occurred_at=datetime.now(UTC),
            )

    def test_unknown_event_type_rejected(self) -> None:
        actor = ProviderAuditActor(id="user-1", label="Operator")
        with pytest.raises(ValidationError):
            ProviderAuditEvent(
                provider_name="cloud-test",
                event_type="not_a_real_event",  # type: ignore[arg-type]
                actor=actor,
                payload={},
                occurred_at=datetime.now(UTC),
            )

    def test_id_optional_on_construction(self) -> None:
        actor = ProviderAuditActor(id="user-1", label="Operator")
        event = ProviderAuditEvent(
            provider_name="cloud-test",
            event_type="provider_updated",
            actor=actor,
            payload={},
            occurred_at=datetime.now(UTC),
        )
        assert event.id is None


@pytest.mark.unit
class TestRateLimitsDtos:
    def test_response_defaults_to_unlimited(self) -> None:
        resp = RateLimitsResponse()
        assert resp.requests_per_minute == 0
        assert resp.concurrent_requests == 0

    def test_update_request_empty_patch_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RateLimitsUpdateRequest()

    def test_update_request_partial(self) -> None:
        req = RateLimitsUpdateRequest(requests_per_minute=60)
        assert req.requests_per_minute == 60
        assert req.concurrent_requests is None

    def test_update_request_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RateLimitsUpdateRequest(requests_per_minute=-1)


@pytest.mark.unit
class TestPresetOverrideDtos:
    def test_override_blank_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PresetOverride(preset_name="")

    def test_override_all_fields_optional(self) -> None:
        override = PresetOverride(preset_name="cloud-test")
        assert override.default_models is None
        assert override.base_url is None
        assert override.candidate_urls is None

    def test_update_empty_patch_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PresetOverrideUpdateRequest()

    def test_update_with_base_url(self) -> None:
        req = PresetOverrideUpdateRequest(base_url="https://api.example.com")
        assert req.base_url == "https://api.example.com"


@pytest.mark.unit
class TestCredentialsRotateRequest:
    def test_api_key_variant(self) -> None:
        from synthorg.api.dto_provider_capabilities import _ApiKeyRotation

        # Pydantic accepts ``str`` for ``SecretStr`` fields and coerces
        # at validation time; the static-type cast keeps Pyright happy.
        rot = _ApiKeyRotation.model_validate(
            {"auth_type": AuthType.API_KEY, "api_key": "secret-key-x"},
        )
        assert rot.auth_type == AuthType.API_KEY
        assert rot.api_key.get_secret_value() == "secret-key-x"

    def test_api_key_too_short_rejected(self) -> None:
        from synthorg.api.dto_provider_capabilities import _ApiKeyRotation

        with pytest.raises(ValidationError):
            _ApiKeyRotation.model_validate(
                {"auth_type": AuthType.API_KEY, "api_key": "abc"},
            )

    def test_oauth_variant(self) -> None:
        from synthorg.api.dto_provider_capabilities import _OAuthRotation

        rot = _OAuthRotation.model_validate(
            {
                "auth_type": AuthType.OAUTH,
                "oauth_token_url": "https://oauth.example.com/token",
                "oauth_client_id": "client-1",
                "oauth_client_secret": "oauth-secret-x",
            },
        )
        assert rot.oauth_scope is None
        assert rot.oauth_client_secret.get_secret_value() == "oauth-secret-x"


@pytest.mark.unit
class TestModelMutationDtos:
    def test_add_model_request(self) -> None:
        model = ProviderModelConfig(id="example-large-001", alias="large")
        req = AddModelRequest(model=model)
        assert req.model.id == "example-large-001"

    def test_sync_request_defaults_replace_existing(self) -> None:
        req = SyncModelsRequest()
        assert req.replace_existing is True
        assert req.preset_hint is None

    def test_sync_request_append_only(self) -> None:
        req = SyncModelsRequest(
            replace_existing=False,
            preset_hint="test-provider",
        )
        assert req.replace_existing is False
        assert req.preset_hint == "test-provider"

    def test_sync_response_construction(self) -> None:
        resp = SyncModelsResponse(
            added=("a",),
            removed=(),
            updated=("b",),
            models=(),
        )
        assert resp.added == ("a",)
        assert resp.updated == ("b",)
