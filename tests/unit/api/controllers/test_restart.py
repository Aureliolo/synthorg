"""Tests for ``/api/v1/meta/restart``."""

from collections.abc import AsyncIterator

import pytest

from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers

_HEADERS = make_auth_headers("ceo")
_PATH = "/api/v1/meta/restart"

# An inert restart-required setting: writing it exercises the derivation
# without changing behaviour any other test depends on.
_RESTART_REQUIRED_PATH = "/api/v1/settings/memory/procedural_max_tokens"


@pytest.fixture
async def restored_setting(
    async_test_client: LoopAsyncClient,
) -> AsyncIterator[None]:
    """Drop the override the test wrote.

    The app is shared across the module, so an override left behind would
    keep every later test's restart status non-empty.
    """
    yield
    await async_test_client.delete(_RESTART_REQUIRED_PATH, headers=_HEADERS)


@pytest.mark.unit
class TestRestartStatus:
    """The dashboard reads whether a restart is owed, rather than remembering."""

    async def test_status_reports_nothing_pending_on_a_fresh_boot(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(_PATH, headers=_HEADERS)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["pending"] == []

    async def test_status_reports_unsupervised_by_default(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """``api.restart_supervised`` defaults false, so the control is hidden.

        A bare run has nothing to start the process again, and offering a
        button whose only outcome is a refusal is worse than not offering it.
        """
        resp = await async_test_client.get(_PATH, headers=_HEADERS)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["supervised"] is False

    @pytest.mark.usefixtures("restored_setting")
    async def test_saving_a_restart_required_setting_makes_it_pending(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """The write, not the client that made it, is what raises the notice."""
        write = await async_test_client.put(
            _RESTART_REQUIRED_PATH,
            headers=_HEADERS,
            json={"value": "2000"},
        )
        assert write.status_code == 200, write.text

        resp = await async_test_client.get(_PATH, headers=_HEADERS)
        assert resp.status_code == 200, resp.text
        pending = resp.json()["data"]["pending"]
        assert [entry["key"] for entry in pending] == ["procedural_max_tokens"]
        assert pending[0]["namespace"] == "memory"
        assert pending[0]["description"]

    @pytest.mark.usefixtures("restored_setting")
    async def test_a_hot_reloadable_write_raises_no_notice(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """Only settings the process reads once at boot can be pending."""
        # The application is module-shared, so the override has to come back
        # off even when an assertion between the write and the delete fails;
        # otherwise one failure here silently reconfigures later tests.
        try:
            write = await async_test_client.put(
                "/api/v1/settings/memory/planning_memory_digest_budget",
                headers=_HEADERS,
                json={"value": "1200"},
            )
            assert write.status_code == 200, write.text

            resp = await async_test_client.get(_PATH, headers=_HEADERS)
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["pending"] == []
        finally:
            await async_test_client.delete(
                "/api/v1/settings/memory/planning_memory_digest_budget",
                headers=_HEADERS,
            )


@pytest.mark.unit
class TestRestartRefusal:
    """An unsupervised process refuses rather than shutting the deployment down."""

    async def test_restart_requires_confirmation(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            _PATH,
            headers=_HEADERS,
            json={"confirm": False},
        )
        assert resp.status_code == 422, resp.text

    async def test_restart_refused_when_unsupervised(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            _PATH,
            headers=_HEADERS,
            json={"confirm": True},
        )
        assert resp.status_code == 409, resp.text
        assert "nothing is configured to restart this process" in resp.text.lower()
