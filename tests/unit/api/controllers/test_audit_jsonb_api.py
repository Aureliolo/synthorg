"""Tests for JSONB query parameters on the audit endpoint.

The fake backend does not implement ``JsonbQueryCapability``, so
all JSONB queries return 422.  Postgres-specific correctness tests
live in the integration test suite.
"""

import pytest

from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers

_BASE = "/api/v1/security/audit"
_HEADERS = make_auth_headers("ceo")


@pytest.mark.unit
class TestJsonbCapabilityFallback:
    """Non-Postgres backends reject JSONB queries with 422."""

    async def test_jsonb_contains_returns_422(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            _BASE,
            params={"jsonb_contains": '{"severity": "high"}'},
            headers=_HEADERS,
        )
        assert resp.status_code == 422
        assert "Postgres" in resp.json()["error"]

    async def test_jsonb_key_exists_returns_422(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            _BASE,
            params={"jsonb_key_exists": "rule_name"},
            headers=_HEADERS,
        )
        assert resp.status_code == 422

    async def test_no_jsonb_params_works(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """Without JSONB params, the standard query path is used."""
        resp = await async_test_client.get(_BASE, headers=_HEADERS)
        assert resp.status_code == 200
