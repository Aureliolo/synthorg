"""Tests for provider controller."""

from typing import Any

import pytest
from litestar.testing import TestClient


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


@pytest.mark.unit
class TestProviderApiKeySecurity:
    def test_provider_api_key_stripped(
        self,
        root_config: Any,
    ) -> None:
        """Verify api_key is stripped from provider responses."""
        from synthorg.api.controllers.providers import _safe_provider
        from synthorg.config.schema import ProviderConfig

        provider = ProviderConfig(
            driver="test-driver",
            api_key="test-placeholder",
        )
        safe = _safe_provider(provider)
        assert safe.api_key is None
