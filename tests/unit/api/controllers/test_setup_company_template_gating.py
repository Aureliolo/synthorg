"""Tier-coverage gate tests for ``POST /api/v1/setup/company``.

Split out from ``test_setup.py`` to keep that module under the
800-line cap. Both tests exercise the issue #1666 B-5 contract:
the wizard refuses to create a company when no providers carry a
tier-classifiable model, and accepts when at least one model is
seeded via the shared ``mock_providers`` fixture.
"""

from typing import Any

import pytest
from litestar.testing import TestClient


@pytest.mark.unit
class TestSetupCompanyTemplateGating:
    """Tier-coverage gate -- creation succeeds with seeded providers, fails without."""

    def test_company_with_template(
        self,
        test_client: TestClient[Any],
    ) -> None:
        # Seed a provider with at least one model so the
        # tier-coverage gate added for issue #1666 B-5 passes; the
        # gate rejects setups that would otherwise produce per-agent
        # ``no_models_available`` warnings during template expansion.
        from tests.unit.api.controllers.conftest import mock_providers

        with mock_providers(test_client):
            resp = test_client.post(
                "/api/v1/setup/company",
                json={
                    "company_name": "My Startup",
                    "template_name": "solo_founder",
                },
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body["success"] is True
            data = body["data"]
            assert data["company_name"] == "My Startup"
            assert data["template_applied"] == "solo_founder"
            assert data["department_count"] >= 1

    def test_company_with_template_rejects_empty_provider_set(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Issue #1666 B-5: tier-coverage gate at the provider step.

        With no providers configured, the setup wizard refuses the
        company creation and returns 422 instead of producing per-agent
        ``template.model_match.failed`` / ``setup.agent.model_not_found``
        warnings during template expansion.
        """
        resp = test_client.post(
            "/api/v1/setup/company",
            json={
                "company_name": "No-Providers Startup",
                "template_name": "solo_founder",
            },
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        assert "no models" in body["error"].lower()
