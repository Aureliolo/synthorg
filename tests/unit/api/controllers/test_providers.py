"""Tests for provider controller."""

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from litestar.testing import TestClient

from synthorg.config.schema import ProviderModelConfig
from synthorg.providers.errors import ProviderNotFoundError
from tests.unit.api.conftest import make_auth_headers

if TYPE_CHECKING:
    from synthorg.api.controllers.providers import ProviderController


@pytest.mark.unit
class TestProviderController:
    def test_list_providers_empty(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/providers")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == []
        assert body["pagination"]["has_more"] is False
        assert body["pagination"]["next_cursor"] is None

    def test_list_providers_tampered_cursor(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/providers?cursor=not-a-valid-cursor")
        assert resp.status_code == 400

    def test_get_provider_not_found(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/providers/nonexistent")
        assert resp.status_code == 404

    def test_list_models_not_found(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/providers/nonexistent/models")
        assert resp.status_code == 404

    def test_oversized_provider_name_rejected(
        self, test_client: TestClient[Any]
    ) -> None:
        long_name = "x" * 129
        resp = test_client.get(f"/api/v1/providers/{long_name}")
        assert resp.status_code == 400


@pytest.mark.unit
class TestProviderResponseSecurity:
    def test_to_provider_response_strips_secrets(self) -> None:
        from synthorg.api.dto import to_provider_response
        from synthorg.config.schema import ProviderConfig

        provider = ProviderConfig(
            driver="test-driver",
            api_key="test-placeholder",
        )
        response = to_provider_response(provider, name=None)
        assert response.has_api_key is True
        # The response should not have api_key attribute at all
        assert (
            not hasattr(response, "api_key") or "api_key" not in response.model_fields
        )

    def test_response_has_credential_indicators(self) -> None:
        from synthorg.api.dto import to_provider_response
        from synthorg.config.schema import ProviderConfig
        from synthorg.providers.enums import AuthType

        provider = ProviderConfig(
            driver="test-driver",
            auth_type=AuthType.CUSTOM_HEADER,
            custom_header_name="X-Auth",
            custom_header_value="secret",
        )
        response = to_provider_response(provider, name=None)
        assert response.has_custom_header is True
        assert response.has_api_key is False
        assert response.has_oauth_credentials is False

    def test_response_never_contains_secrets(self) -> None:
        from synthorg.api.dto import to_provider_response
        from synthorg.config.schema import ProviderConfig
        from synthorg.providers.enums import AuthType

        provider = ProviderConfig(
            driver="test-driver",
            auth_type=AuthType.OAUTH,
            api_key="secret-key",
            oauth_token_url="https://auth.example.com/token",
            oauth_client_id="client-id",
            oauth_client_secret="secret-value",
        )
        response = to_provider_response(provider, name=None)
        dumped = response.model_dump()
        all_values = json.dumps(dumped)
        assert "secret-key" not in all_values
        assert "secret-value" not in all_values
        # oauth_client_id is intentionally non-secret (included for frontend UX)
        assert "client-id" in all_values


@pytest.mark.unit
class TestProviderCrudEndpoints:
    def test_get_presets_returns_all(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get("/api/v1/providers/presets")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["data"]) >= 4

    def test_write_endpoints_require_write_access(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.post(
            "/api/v1/providers",
            json={
                "name": "test-provider",
                "driver": "litellm",
                "auth_type": "none",
            },
            headers=make_auth_headers("observer"),
        )
        assert resp.status_code == 403

    def test_probe_local_requires_write_access(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """POST /providers/probe-local is guarded by write access."""
        resp = test_client.post(
            "/api/v1/providers/probe-local",
            json={},
            headers=make_auth_headers("observer"),
        )
        assert resp.status_code == 403

    def test_legacy_probe_preset_endpoint_returns_404(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """The legacy /probe-preset endpoint is removed and must 404 / 405.

        Belt-and-braces regression guard so a stray client integration
        cannot silently fall through to a different handler.
        """
        resp = test_client.post(
            "/api/v1/providers/probe-preset",
            json={"preset_name": "ollama"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code in (404, 405)


def _make_provider_state_and_mgmt() -> tuple[MagicMock, AsyncMock]:
    """Create a mock Litestar State with a mock provider management service.

    Returns:
        Tuple of (mock_state, mock_provider_management).
    """
    from litestar.datastructures import State

    from synthorg.api.state import AppState
    from synthorg.providers.management.service import ProviderManagementService

    mgmt = AsyncMock(spec=ProviderManagementService)
    app_state = MagicMock(spec=AppState)
    app_state.provider_management = mgmt

    state = MagicMock(spec=State)
    state.app_state = app_state
    return state, mgmt


def _provider_controller() -> ProviderController:
    """Create a ProviderController instance for testing."""
    from synthorg.api.controllers.providers import ProviderController

    return ProviderController(owner=ProviderController)  # type: ignore[arg-type]


@pytest.mark.unit
class TestDiscoverModelsEndpoint:
    """Tests for POST /providers/{name}/discover-models."""

    async def test_discover_models_success(self) -> None:
        """Successful discovery returns models and provider name."""
        state, mgmt = _make_provider_state_and_mgmt()
        discovered = (
            ProviderModelConfig(id="test-model-a"),
            ProviderModelConfig(id="test-model-b"),
        )
        mgmt.discover_models_for_provider = AsyncMock(
            return_value=discovered,
        )

        ctrl = _provider_controller()
        result = await ctrl.discover_models.fn(
            ctrl,
            state=state,
            name="test-provider",
        )

        mgmt.discover_models_for_provider.assert_awaited_once_with(
            "test-provider",
            preset_hint=None,
        )
        assert result.data.provider_name == "test-provider"
        assert result.data.discovered_models == discovered

    async def test_discover_models_not_found(self) -> None:
        """Non-existent provider raises NotFoundError."""
        from synthorg.core.domain_errors import NotFoundError

        state, mgmt = _make_provider_state_and_mgmt()
        mgmt.discover_models_for_provider = AsyncMock(
            side_effect=ProviderNotFoundError("Provider 'nonexistent' not found"),
        )

        ctrl = _provider_controller()
        with pytest.raises(NotFoundError):
            await ctrl.discover_models.fn(
                ctrl,
                state=state,
                name="nonexistent",
            )


@pytest.mark.unit
class TestListModelsBatchCapabilities:
    """``list_models`` must batch capability lookups, not loop per-model."""

    async def test_calls_batch_get_capabilities_once(self) -> None:
        from synthorg.config.schema import ProviderConfig
        from synthorg.providers.capabilities import ModelCapabilities

        models = (
            ProviderModelConfig(id="m1"),
            ProviderModelConfig(id="m2"),
            ProviderModelConfig(id="m3"),
        )
        provider = ProviderConfig(driver="test-driver", models=models)
        caps_lookup = {
            "m1": ModelCapabilities(
                model_id="m1",
                provider="test-provider",
                max_context_tokens=1024,
                max_output_tokens=512,
                cost_per_1k_input=0.001,
                cost_per_1k_output=0.002,
            ),
            "m2": None,
            "m3": ModelCapabilities(
                model_id="m3",
                provider="test-provider",
                max_context_tokens=2048,
                max_output_tokens=1024,
                cost_per_1k_input=0.003,
                cost_per_1k_output=0.004,
            ),
        }

        driver = MagicMock()
        driver.batch_get_capabilities = AsyncMock(return_value=caps_lookup)
        # Single-model lookup must NOT be called from the batched path.
        driver.get_model_capabilities = AsyncMock(
            side_effect=AssertionError("per-model lookup should not run"),
        )

        state, _ = _make_provider_state_and_mgmt()
        state.app_state.config_resolver.get_provider_configs = AsyncMock(
            return_value={"test-provider": provider},
        )
        state.app_state.has_provider_registry = True
        state.app_state.provider_registry = {"test-provider": driver}

        ctrl = _provider_controller()
        result = await ctrl.list_models.fn(
            ctrl,
            state=state,
            name="test-provider",
        )

        driver.batch_get_capabilities.assert_awaited_once_with(("m1", "m2", "m3"))
        driver.get_model_capabilities.assert_not_awaited()
        assert len(result.data) == 3

    async def test_no_driver_skips_capability_lookup(self) -> None:
        from synthorg.config.schema import ProviderConfig

        provider = ProviderConfig(
            driver="test-driver",
            models=(ProviderModelConfig(id="only"),),
        )
        state, _ = _make_provider_state_and_mgmt()
        state.app_state.config_resolver.get_provider_configs = AsyncMock(
            return_value={"test-provider": provider},
        )
        state.app_state.has_provider_registry = False

        ctrl = _provider_controller()
        result = await ctrl.list_models.fn(
            ctrl,
            state=state,
            name="test-provider",
        )

        assert len(result.data) == 1


@pytest.mark.unit
class TestProbeLocalResponseInvariant:
    """Tests for the disjoint-set invariant on ``ProbeLocalResponse``."""

    def test_overlapping_results_and_errors_raises(self) -> None:
        """Constructing the model with the same name in both maps fails fast."""
        from pydantic import ValidationError

        from synthorg.api.dto import ProbeLocalResponse, ProbePresetResponse

        with pytest.raises(ValidationError, match=r"results.*errors.*overlap"):
            ProbeLocalResponse(
                results={
                    "test-local-a": ProbePresetResponse(
                        url="http://example/a",
                        model_count=1,
                        candidates_tried=1,
                    ),
                },
                errors={"test-local-a": "boom"},
            )

    def test_disjoint_results_and_errors_validate(self) -> None:
        """Disjoint maps construct cleanly (no validation error)."""
        from synthorg.api.dto import ProbeLocalResponse, ProbePresetResponse

        response = ProbeLocalResponse(
            results={
                "test-local-a": ProbePresetResponse(
                    url="http://example/a",
                    model_count=1,
                    candidates_tried=1,
                ),
            },
            errors={"test-local-b": "boom"},
        )
        assert "test-local-a" in response.results
        assert "test-local-b" in response.errors


@pytest.mark.unit
class TestProbeLocalEndpoint:
    """Tests for POST /providers/probe-local (batch local probe).

    These cases assert against the real ``PROVIDER_PRESETS`` registry
    via ``list_probable_presets()``, so they reference the actual
    preset names ("ollama", "lm-studio", "vllm").  The CLAUDE.md
    "test-provider" rule covers freshly-authored test fixtures, not
    references to the registry under test -- replacing the names here
    would require mocking ``list_probable_presets`` in every case and
    lose the registry-integration assertion (catching e.g. a future
    rename, accidental drop, or new preset that quietly slips in).
    """

    async def test_all_local_presets_succeed(self) -> None:
        """Every probable preset's result lands in ``results``, none in ``errors``."""
        from unittest.mock import patch

        from synthorg.providers.presets import list_probable_presets
        from synthorg.providers.probing import ProbeResult

        state, _ = _make_provider_state_and_mgmt()
        ctrl = _provider_controller()

        async def fake_probe(name: str) -> ProbeResult:
            return ProbeResult(
                url=f"http://host:port/{name}",
                model_count=2,
                candidates_tried=1,
            )

        with patch(
            "synthorg.api.controllers.providers.probe_preset_urls",
            side_effect=fake_probe,
        ):
            response = await ctrl.probe_local.fn(ctrl, state=state)

        probable = list_probable_presets()
        expected_names = {p.name for p in probable}
        assert set(response.data.results.keys()) == expected_names
        assert response.data.errors == {}
        for name, entry in response.data.results.items():
            assert entry.url == f"http://host:port/{name}"
            assert entry.model_count == 2
            assert entry.candidates_tried == 1

    async def test_partial_failure_records_errors(self) -> None:
        """One preset raising does not abort the batch; other results land."""
        from unittest.mock import patch

        from synthorg.providers.probing import ProbeResult

        state, _ = _make_provider_state_and_mgmt()
        ctrl = _provider_controller()

        async def fake_probe(name: str) -> ProbeResult:
            if name == "ollama":
                msg = "boom"
                raise RuntimeError(msg)
            return ProbeResult(
                url=f"http://host:port/{name}",
                model_count=1,
                candidates_tried=1,
            )

        with patch(
            "synthorg.api.controllers.providers.probe_preset_urls",
            side_effect=fake_probe,
        ):
            response = await ctrl.probe_local.fn(ctrl, state=state)

        assert "ollama" in response.data.errors
        assert "ollama" not in response.data.results
        # LM Studio should still appear in results despite Ollama's failure.
        assert "lm-studio" in response.data.results

    async def test_all_failures_records_all_errors(self) -> None:
        """When every preset raises, every entry lives under ``errors``."""
        from unittest.mock import patch

        from synthorg.providers.presets import list_probable_presets

        state, _ = _make_provider_state_and_mgmt()
        ctrl = _provider_controller()

        async def fake_probe(_name: str) -> object:
            msg = "all down"
            raise RuntimeError(msg)

        with patch(
            "synthorg.api.controllers.providers.probe_preset_urls",
            side_effect=fake_probe,
        ):
            response = await ctrl.probe_local.fn(ctrl, state=state)

        probable = list_probable_presets()
        expected_names = {p.name for p in probable}
        assert set(response.data.errors.keys()) == expected_names
        assert response.data.results == {}

    async def test_excludes_vllm(self) -> None:
        """vLLM has no candidate URLs and must not appear in either map."""
        from unittest.mock import patch

        from synthorg.providers.probing import ProbeResult

        state, _ = _make_provider_state_and_mgmt()
        ctrl = _provider_controller()

        async def fake_probe(name: str) -> ProbeResult:
            return ProbeResult(
                url=f"http://probe/{name}",
                model_count=0,
                candidates_tried=1,
            )

        with patch(
            "synthorg.api.controllers.providers.probe_preset_urls",
            side_effect=fake_probe,
        ):
            response = await ctrl.probe_local.fn(ctrl, state=state)

        assert "vllm" not in response.data.results
        assert "vllm" not in response.data.errors

    async def test_empty_probable_list_returns_empty_envelope(self) -> None:
        """No probable presets => empty envelope, never raises.

        Defensive guard against the silent-degradation case where a
        future refactor empties every local preset's candidate_urls.
        """
        from unittest.mock import patch

        state, _ = _make_provider_state_and_mgmt()
        ctrl = _provider_controller()

        with patch(
            "synthorg.api.controllers.providers.list_probable_presets",
            return_value=(),
        ):
            response = await ctrl.probe_local.fn(ctrl, state=state)

        assert response.data.results == {}
        assert response.data.errors == {}

    def test_probe_local_rate_limit_returns_429_when_exhausted(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """The probe-local guard surfaces a 429 once its bucket drains.

        Drains the (20, 60) per-user bucket and asserts the next call
        receives a 429.  Locks the rate-limit policy in place so a
        future refactor can't silently remove the guard from the
        controller.  ``probe_preset_urls`` is mocked so each call
        returns instantly without any real HTTP traffic.
        """
        from unittest.mock import patch

        from synthorg.providers.probing import ProbeResult

        async def fast_probe(_name: str) -> ProbeResult:
            return ProbeResult(url=None, model_count=0, candidates_tried=0)

        with patch(
            "synthorg.api.controllers.providers.probe_preset_urls",
            side_effect=fast_probe,
        ):
            # Drain the bucket; one user, sequential calls.  Each
            # admit must return a clean 2xx -- a 5xx would mean the
            # handler regressed and is hiding behind "not 429".
            # Litestar ``@post`` defaults to 201 Created on success;
            # accept either 200 OR 201 to stay handler-config agnostic.
            for i in range(20):
                resp = test_client.post(
                    "/api/v1/providers/probe-local",
                    json={},
                    headers=make_auth_headers("ceo"),
                )
                assert resp.status_code in (200, 201), (
                    f"Probe call {i + 1}/20 returned "
                    f"{resp.status_code}; expected 2xx while bucket fills"
                )
            # 21st call hits the cap.
            resp = test_client.post(
                "/api/v1/providers/probe-local",
                json={},
                headers=make_auth_headers("ceo"),
            )
            assert resp.status_code == 429


@pytest.mark.unit
class TestProviderControllerErrorSanitization:
    """Validation / conflict error paths must surface sanitized text.

    The controller wraps backend ``ProviderValidationError`` /
    ``ProviderAlreadyExistsError`` into Litestar's
    ``ValidationError`` / ``ConflictError`` so the API client gets a
    structured 4xx.  The detail string MUST go through
    ``safe_error_description`` so a backend message that embeds a
    credential, file path, or stack-trace fragment cannot leak
    through the HTTP envelope.
    """

    @staticmethod
    def _safe(exc: BaseException) -> str:
        from synthorg.observability import safe_error_description

        return safe_error_description(exc)

    async def test_create_provider_conflict_uses_sanitized_text(self) -> None:
        from synthorg.core.domain_errors import ConflictError
        from synthorg.providers.errors import ProviderAlreadyExistsError
        from synthorg.providers.management.dtos import CreateProviderRequest

        state, mgmt = _make_provider_state_and_mgmt()
        boom = ProviderAlreadyExistsError(
            "Provider 'test-provider' already exists at /etc/secrets/api.key",
        )
        mgmt.create_provider.side_effect = boom

        ctrl = _provider_controller()
        with pytest.raises(ConflictError) as info:
            await ctrl.create_provider.fn(
                ctrl,
                state=state,
                data=CreateProviderRequest(name="test-provider"),
            )
        assert str(info.value) == self._safe(boom)

    async def test_create_provider_validation_uses_sanitized_text(self) -> None:
        from synthorg.core.domain_errors import ValidationError
        from synthorg.providers.errors import ProviderValidationError
        from synthorg.providers.management.dtos import CreateProviderRequest

        state, mgmt = _make_provider_state_and_mgmt()
        boom = ProviderValidationError(
            "base_url 'http://10.0.0.1/secrets' rejected by guard",
        )
        mgmt.create_provider.side_effect = boom

        ctrl = _provider_controller()
        with pytest.raises(ValidationError) as info:
            await ctrl.create_provider.fn(
                ctrl,
                state=state,
                data=CreateProviderRequest(name="test-provider"),
            )
        assert str(info.value) == self._safe(boom)

    async def test_create_from_preset_conflict_uses_sanitized_text(self) -> None:
        from synthorg.core.domain_errors import ConflictError
        from synthorg.providers.errors import ProviderAlreadyExistsError
        from synthorg.providers.management.dtos import CreateFromPresetRequest

        state, mgmt = _make_provider_state_and_mgmt()
        boom = ProviderAlreadyExistsError("already configured")
        mgmt.create_from_preset.side_effect = boom

        ctrl = _provider_controller()
        with pytest.raises(ConflictError) as info:
            await ctrl.create_from_preset.fn(
                ctrl,
                state=state,
                data=CreateFromPresetRequest(
                    name="test-provider",
                    preset_name="ollama",
                ),
            )
        assert str(info.value) == self._safe(boom)

    async def test_create_from_preset_validation_uses_sanitized_text(
        self,
    ) -> None:
        from synthorg.core.domain_errors import ValidationError
        from synthorg.providers.errors import ProviderValidationError
        from synthorg.providers.management.dtos import CreateFromPresetRequest

        state, mgmt = _make_provider_state_and_mgmt()
        boom = ProviderValidationError("preset 'ollama' missing capability")
        mgmt.create_from_preset.side_effect = boom

        ctrl = _provider_controller()
        with pytest.raises(ValidationError) as info:
            await ctrl.create_from_preset.fn(
                ctrl,
                state=state,
                data=CreateFromPresetRequest(
                    name="test-provider",
                    preset_name="ollama",
                ),
            )
        assert str(info.value) == self._safe(boom)

    async def test_update_provider_validation_uses_sanitized_text(self) -> None:
        from synthorg.core.domain_errors import ValidationError
        from synthorg.providers.errors import ProviderValidationError
        from synthorg.providers.management.dtos import UpdateProviderRequest

        state, mgmt = _make_provider_state_and_mgmt()
        boom = ProviderValidationError("oauth_token_url rejected")
        mgmt.update_provider.side_effect = boom

        ctrl = _provider_controller()
        with pytest.raises(ValidationError) as info:
            await ctrl.update_provider.fn(
                ctrl,
                state=state,
                name="test-provider",
                data=UpdateProviderRequest(),
            )
        assert str(info.value) == self._safe(boom)

    async def test_delete_model_validation_uses_sanitized_text(self) -> None:
        from synthorg.core.domain_errors import ValidationError
        from synthorg.providers.errors import ProviderValidationError

        state, mgmt = _make_provider_state_and_mgmt()
        boom = ProviderValidationError("model still in use")
        mgmt.delete_model.side_effect = boom

        ctrl = _provider_controller()
        with pytest.raises(ValidationError) as info:
            await ctrl.delete_model.fn(
                ctrl,
                state=state,
                name="test-provider",
                model_id="test-small-001",
            )
        assert str(info.value) == self._safe(boom)

    async def test_delete_model_runtime_uses_sanitized_text(self) -> None:
        from synthorg.core.domain_errors import DomainError

        state, mgmt = _make_provider_state_and_mgmt()
        boom = RuntimeError("internal driver state /var/run/.cache/0xdeadbeef")
        mgmt.delete_model.side_effect = boom

        ctrl = _provider_controller()
        with pytest.raises(DomainError) as info:
            await ctrl.delete_model.fn(
                ctrl,
                state=state,
                name="test-provider",
                model_id="test-small-001",
            )
        assert str(info.value) == self._safe(boom)

    @pytest.mark.parametrize(
        "message",
        [
            "Model 'missing' not found in provider 'test-provider'",
            "model 'missing' not found",
            "Internal model registry mismatch: id 'missing' absent",
        ],
    )
    async def test_update_model_config_model_missing_uses_sanitized_text(
        self,
        message: str,
    ) -> None:
        from synthorg.config.provider_schema import LocalModelParams
        from synthorg.core.domain_errors import NotFoundError
        from synthorg.providers.errors import ProviderModelNotFoundError
        from synthorg.providers.management.dtos import UpdateModelConfigRequest

        state, mgmt = _make_provider_state_and_mgmt()
        boom = ProviderModelNotFoundError(message)
        mgmt.update_model_config.side_effect = boom

        ctrl = _provider_controller()
        with pytest.raises(NotFoundError) as info:
            await ctrl.update_model_config.fn(
                ctrl,
                state=state,
                name="test-provider",
                model_id="missing",
                data=UpdateModelConfigRequest(local_params=LocalModelParams()),
            )
        assert str(info.value) == self._safe(boom)

    async def test_update_model_config_validation_uses_sanitized_text(
        self,
    ) -> None:
        from synthorg.config.provider_schema import LocalModelParams
        from synthorg.core.domain_errors import ValidationError
        from synthorg.providers.errors import ProviderValidationError
        from synthorg.providers.management.dtos import UpdateModelConfigRequest

        state, mgmt = _make_provider_state_and_mgmt()
        boom = ProviderValidationError("num_ctx must be positive")
        mgmt.update_model_config.side_effect = boom

        ctrl = _provider_controller()
        with pytest.raises(ValidationError) as info:
            await ctrl.update_model_config.fn(
                ctrl,
                state=state,
                name="test-provider",
                model_id="test-small-001",
                data=UpdateModelConfigRequest(local_params=LocalModelParams()),
            )
        assert str(info.value) == self._safe(boom)

    async def test_capability_mutations_sanitize_validation_text(self) -> None:
        """Three capability mutations all sanitize ProviderValidationError text.

        Drives the real ``audit_actor_from_context`` code path via
        ``authenticated_user_scope`` rather than monkey-patching the
        import; a future refactor that drops the import will surface
        as a real ``AuthContextMissingError`` rather than a silent
        no-op patch.
        """
        from pydantic import SecretStr

        from synthorg.api.auth import authenticated_user_scope
        from synthorg.core.auth.models import AuthenticatedUser, AuthMethod
        from synthorg.core.auth.roles import HumanRole
        from synthorg.core.domain_errors import ValidationError
        from synthorg.providers.enums import AuthType
        from synthorg.providers.errors import ProviderValidationError
        from synthorg.providers.management.capability_dtos import (
            RateLimitsUpdateRequest,
            SyncModelsRequest,
            _ApiKeyRotation,
        )

        cases = [
            (
                "rotate_credentials",
                "auth_type mismatch",
                _ApiKeyRotation(
                    auth_type=AuthType.API_KEY,
                    api_key=SecretStr("test-key"),
                ),
            ),
            (
                "update_rate_limits",
                "requests_per_minute too low",
                RateLimitsUpdateRequest(requests_per_minute=10),
            ),
            (
                "sync_models",
                "base_url is required",
                SyncModelsRequest(),
            ),
        ]
        user = AuthenticatedUser(
            user_id="actor",
            username="actor@example.com",
            role=HumanRole.CEO,
            auth_method=AuthMethod.JWT,
        )

        for mutation_name, error_msg, data in cases:
            state, mgmt = _make_provider_state_and_mgmt()
            boom = ProviderValidationError(error_msg)
            getattr(mgmt, mutation_name).side_effect = boom
            ctrl = _provider_controller()
            handler = getattr(ctrl, mutation_name).fn
            async with authenticated_user_scope(user):
                with pytest.raises(ValidationError) as info:
                    await handler(
                        ctrl,
                        state=state,
                        name="test-provider",
                        data=data,
                    )
            assert str(info.value) == self._safe(boom), f"mutation={mutation_name!r}"
