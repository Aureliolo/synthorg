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
        assert body["data"] == {}

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
        response = to_provider_response(provider)
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
        response = to_provider_response(provider)
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
        response = to_provider_response(provider)
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
    mgmt = AsyncMock()
    app_state = MagicMock()
    app_state.provider_management = mgmt

    state = MagicMock()
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
        from synthorg.api.errors import NotFoundError

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
class TestProbeLocalEndpoint:
    """Tests for POST /providers/probe-local (batch local probe)."""

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
            # Drain the bucket; one user, sequential calls.
            for _ in range(20):
                resp = test_client.post(
                    "/api/v1/providers/probe-local",
                    json={},
                    headers=make_auth_headers("ceo"),
                )
                # 200 (success), 502/503 (transient) all mean "the
                # guard let it through".  429 here would invalidate
                # the test setup.
                assert resp.status_code != 429, (
                    "Bucket drained earlier than expected -- check fixture"
                )
            # 21st call hits the cap.
            resp = test_client.post(
                "/api/v1/providers/probe-local",
                json={},
                headers=make_auth_headers("ceo"),
            )
            assert resp.status_code == 429
