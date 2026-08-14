"""Tier-coverage gate tests for ``POST /api/v1/setup/company``.

Split out from ``test_setup.py`` to keep that module under the
800-line cap. Both tests exercise the contract that the wizard
refuses to create a company when no providers carry a
tier-classifiable model, and accepts when at least one model is
seeded via the shared ``mock_providers`` fixture.
"""

from unittest.mock import AsyncMock, patch

import pytest
from structlog.testing import capture_logs

from synthorg.observability.events.setup import SETUP_POSTURE_SEED_FAILED
from tests._shared import LoopAsyncClient


@pytest.mark.unit
class TestSetupCompanyTemplateGating:
    """Tier-coverage gate -- creation succeeds with seeded providers, fails without."""

    async def test_company_with_template(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        # Seed a provider with at least one model so the
        # tier-coverage gate passes; the gate rejects setups that
        # would otherwise produce per-agent ``no_models_available``
        # warnings during template expansion.
        from tests.unit.api.controllers.conftest import mock_providers

        with mock_providers(async_test_client):
            resp = await async_test_client.post(
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

    async def test_posture_seed_failure_is_non_fatal(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """A posture-seed failure logs a WARNING but setup still succeeds."""
        from tests.unit.api.controllers.conftest import mock_providers

        boom = RuntimeError("posture seed exploded")
        with (
            mock_providers(async_test_client),
            patch(
                "synthorg.api.controllers.setup.company._seed_posture_settings",
                new_callable=AsyncMock,
                side_effect=boom,
            ),
            capture_logs() as logs,
        ):
            resp = await async_test_client.post(
                "/api/v1/setup/company",
                json={
                    "company_name": "Resilient Co",
                    "template_name": "solo_founder",
                },
            )

        assert resp.status_code == 201
        assert resp.json()["success"] is True
        failures = [e for e in logs if e.get("event") == SETUP_POSTURE_SEED_FAILED]
        assert len(failures) == 1
        assert failures[0]["error_type"] == "RuntimeError"

    async def test_company_with_template_rejects_empty_provider_set(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """Tier-coverage gate at the provider step.

        With no providers configured, the setup wizard refuses the
        company creation and returns 422 with a discriminated
        ``error_code`` (``PROVIDER_MODEL_COVERAGE_INSUFFICIENT`` = 2004)
        so the dashboard can route the operator back to the providers
        step instead of showing a generic Retry button. The reworded
        message points at the upstream Providers step rather than the
        company step.
        """
        resp = await async_test_client.post(
            "/api/v1/setup/company",
            json={
                "company_name": "No-Providers Startup",
                "template_name": "solo_founder",
            },
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        # Discriminated error_code lets the dashboard match without
        # parsing the human-readable message.
        assert body["error_detail"]["error_code"] == 2004
        # Message routes the user back to the Providers step.
        message = body["error"].lower()
        assert "providers step" in message
        assert "model" in message
