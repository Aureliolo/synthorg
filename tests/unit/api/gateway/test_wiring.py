"""Tests for gateway boot wiring: construction, predicate, auth-exclude, binding."""

import pytest

from synthorg.api.construction_wiring import ConstructionDeps
from synthorg.api.gateway._construction import wire_construction
from synthorg.api.gateway.state import GatewayStateSlice
from synthorg.api.middleware_factory import _build_auth_exclude_paths
from synthorg.api.route_predicates import gateway_controller_ready
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.core.auth.config import AuthConfig
from synthorg.llm.gateway_binding import mint_run_token
from synthorg.llm.gateway_errors import GatewayModelUnboundError
from synthorg.llm.gateway_token import GatewaySigner
from synthorg.settings.model_ref import ModelRef
from tests._shared import FakeClock, mock_of

pytestmark = pytest.mark.unit

_SECRET = b"w" * 32


def _make_state() -> AppState:
    return AppState(config=RootConfig(company_name="test"))


def test_wire_construction_populates_service_and_signer() -> None:
    state = _make_state()

    wire_construction(state, mock_of[ConstructionDeps]())

    slice_ = state.slice(GatewayStateSlice)
    assert slice_.service is not None
    assert slice_.signer is not None


def test_predicate_false_until_wired_then_true() -> None:
    state = _make_state()
    assert gateway_controller_ready(state) is False

    wire_construction(state, mock_of[ConstructionDeps]())

    assert gateway_controller_ready(state) is True


def test_auth_exclude_paths_include_the_gateway() -> None:
    paths = _build_auth_exclude_paths(AuthConfig(), "/api", "^/ws$")

    # Anchored with ``(/|$)`` so only the gateway route + sub-paths match,
    # never a fail-open sibling like ``/api/gateway-admin``.
    assert "^/api/gateway(/|$)" in paths


def test_mint_run_token_binds_explicit_provider_and_model() -> None:
    signer = GatewaySigner(secret=_SECRET, clock=FakeClock())
    ref = ModelRef(provider="example-provider", model_id="example-large-001")

    token = mint_run_token(
        signer,
        execution_id="exec-1",
        agent_id="agent-1",
        task_id="task-1",
        ref=ref,
        ttl_seconds=60,
    )

    claims = signer.verify(token)
    assert claims.provider == "example-provider"
    assert claims.model_id == "example-large-001"


def test_mint_run_token_rejects_an_unbound_ref() -> None:
    signer = GatewaySigner(secret=_SECRET, clock=FakeClock())
    unbound = ModelRef(model_id="example-large-001")

    with pytest.raises(GatewayModelUnboundError):
        mint_run_token(
            signer,
            execution_id="exec-1",
            agent_id="agent-1",
            task_id="task-1",
            ref=unbound,
            ttl_seconds=60,
        )
