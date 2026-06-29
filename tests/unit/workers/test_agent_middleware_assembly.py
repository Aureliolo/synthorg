"""Unit tests for the boot agent-middleware-chain assembly.

Covers the disabled path (no chain) and the fail-closed contract: when the
operator opts into the chain via ``engine.enable_agent_middleware`` but the
build fails, startup must abort rather than silently run the agent path
without the authority-deference defence.
"""

import pytest

from synthorg.engine.middleware import factory as mw_factory
from synthorg.workers._agent_middleware_assembly import (
    build_agent_middleware_chain_or_none,
)
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def test_returns_none_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNTHORG_ENGINE_ENABLE_AGENT_MIDDLEWARE", "false")
    app_state = make_app_state()
    assert (
        build_agent_middleware_chain_or_none(app_state, error_taxonomy_config=None)
        is None
    )


def test_fails_closed_when_build_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # The operator opted into the safety chain, so a build failure must
    # propagate (abort startup) rather than degrade to an unprotected engine.
    monkeypatch.setenv("SYNTHORG_ENGINE_ENABLE_AGENT_MIDDLEWARE", "true")

    def _boom(*_args: object, **_kwargs: object) -> object:
        msg = "deps incomplete"
        raise RuntimeError(msg)

    monkeypatch.setattr(mw_factory, "build_agent_middleware_chain", _boom)
    app_state = make_app_state()
    with pytest.raises(RuntimeError, match="deps incomplete"):
        build_agent_middleware_chain_or_none(app_state, error_taxonomy_config=None)
