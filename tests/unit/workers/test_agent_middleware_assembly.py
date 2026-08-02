"""Unit tests for the boot agent-middleware-chain assembly.

Covers the disabled path (no chain) and the fail-closed contract: when the
operator opts into the chain via ``engine.enable_agent_middleware`` but the
build fails, startup must abort rather than silently run the agent path
without the authority-deference defence. The flag is read through the
DB-backed resolver, so an operator toggling it takes effect on the next
runtime rebuild rather than the next restart.
"""

from collections.abc import AsyncIterator

import pytest

from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.engine.middleware import factory as mw_factory
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.workers._agent_middleware_assembly import (
    build_agent_middleware_chain_or_none,
)
from tests._shared import make_app_state
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.unit


@pytest.fixture
async def app_state() -> AsyncIterator[AppState]:
    """Yield an app state whose resolver reads DB > env > code default."""
    backend = FakePersistenceBackend()
    await backend.connect()
    yield make_app_state(
        config_resolver=ConfigResolver(
            settings_service=SettingsService(
                repository=backend.settings, registry=get_registry()
            ),
            config=RootConfig(company_name="test"),
        ),
    )
    await backend.disconnect()


async def test_returns_none_when_disabled(
    app_state: AppState, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SYNTHORG_ENGINE_ENABLE_AGENT_MIDDLEWARE", "false")
    chain = await build_agent_middleware_chain_or_none(
        app_state, error_taxonomy_config=None
    )
    assert chain is None


async def test_fails_closed_when_build_raises(
    app_state: AppState, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The operator opted into the safety chain, so a build failure must
    # propagate (abort startup) rather than degrade to an unprotected engine.
    monkeypatch.setenv("SYNTHORG_ENGINE_ENABLE_AGENT_MIDDLEWARE", "true")

    def _boom(*_args: object, **_kwargs: object) -> object:
        msg = "deps incomplete"
        raise RuntimeError(msg)

    monkeypatch.setattr(mw_factory, "build_agent_middleware_chain", _boom)
    with pytest.raises(RuntimeError, match="deps incomplete"):
        await build_agent_middleware_chain_or_none(
            app_state, error_taxonomy_config=None
        )


async def test_an_unwired_resolver_fails_rather_than_running_unprotected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reading the flag is what decides whether the defence is required, so a
    # boot that cannot read it must not answer "no chain": that is the exact
    # shape of silently running the agent path unprotected.
    monkeypatch.setenv("SYNTHORG_ENGINE_ENABLE_AGENT_MIDDLEWARE", "true")
    with pytest.raises(ServiceUnavailableError):
        await build_agent_middleware_chain_or_none(
            make_app_state(), error_taxonomy_config=None
        )
