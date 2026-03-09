"""Tests for application factory."""

from typing import Any

import pytest
from litestar import Litestar
from litestar.testing import TestClient  # noqa: TC002

from ai_company.api.app import create_app


@pytest.mark.unit
class TestCreateApp:
    def test_returns_litestar_instance(
        self,
        fake_persistence: Any,
        fake_message_bus: Any,
        cost_tracker: Any,
        root_config: Any,
    ) -> None:
        app = create_app(
            config=root_config,
            persistence=fake_persistence,
            message_bus=fake_message_bus,
            cost_tracker=cost_tracker,
        )
        assert isinstance(app, Litestar)

    def test_openapi_schema_accessible(self, test_client: TestClient[Any]) -> None:
        response = test_client.get("/schema/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert data["info"]["title"] == "AI Company API"
