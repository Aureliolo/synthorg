"""Unit tests for the gateway per-run signed bearer."""

import secrets

import pytest

from synthorg.llm.gateway_errors import GatewayTokenInvalidError
from synthorg.llm.gateway_token import GatewaySigner, GatewayTokenClaims
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_SECRET = b"0" * 32


def _claims() -> GatewayTokenClaims:
    return GatewayTokenClaims(
        execution_id="exec-1",
        agent_id="agent-1",
        task_id="task-1",
        project_id="project-1",
        provider="example-provider",
        model_id="example-large-001",
        cost_ceiling=1.5,
    )


def test_mint_then_verify_round_trips_claims() -> None:
    signer = GatewaySigner(secret=_SECRET, clock=FakeClock())
    token = signer.mint(_claims(), ttl_seconds=60)

    recovered = signer.verify(token)

    assert recovered == _claims()


def test_expired_token_is_rejected() -> None:
    clock = FakeClock()
    signer = GatewaySigner(secret=_SECRET, clock=clock)
    token = signer.mint(_claims(), ttl_seconds=30)

    clock.advance(31)

    with pytest.raises(GatewayTokenInvalidError):
        signer.verify(token)


def test_token_valid_until_the_expiry_boundary() -> None:
    clock = FakeClock()
    signer = GatewaySigner(secret=_SECRET, clock=clock)
    token = signer.mint(_claims(), ttl_seconds=30)

    clock.advance(29)

    assert signer.verify(token) == _claims()


def test_tampered_payload_is_rejected() -> None:
    signer = GatewaySigner(secret=_SECRET, clock=FakeClock())
    token = signer.mint(_claims(), ttl_seconds=60)
    payload, signature = token.split(".")
    forged = f"{payload}x.{signature}"

    with pytest.raises(GatewayTokenInvalidError):
        signer.verify(forged)


def test_signature_from_a_different_key_is_rejected() -> None:
    minted = GatewaySigner(secret=_SECRET, clock=FakeClock()).mint(
        _claims(), ttl_seconds=60
    )
    other = GatewaySigner(secret=secrets.token_bytes(32), clock=FakeClock())

    with pytest.raises(GatewayTokenInvalidError):
        other.verify(minted)


@pytest.mark.parametrize("bad", ["", "no-separator", "a.b.c", "@@@.@@@"])
def test_malformed_tokens_are_rejected(bad: str) -> None:
    signer = GatewaySigner(secret=_SECRET, clock=FakeClock())

    with pytest.raises(GatewayTokenInvalidError):
        signer.verify(bad)


def test_short_secret_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        GatewaySigner(secret=b"tooshort")


def test_non_positive_ttl_is_rejected() -> None:
    signer = GatewaySigner(secret=_SECRET, clock=FakeClock())

    with pytest.raises(ValueError, match="ttl_seconds must be positive"):
        signer.mint(_claims(), ttl_seconds=0)


def test_with_random_key_produces_distinct_signers() -> None:
    first = GatewaySigner.with_random_key(clock=FakeClock())
    token = first.mint(_claims(), ttl_seconds=60)
    second = GatewaySigner.with_random_key(clock=FakeClock())

    assert first.verify(token) == _claims()
    with pytest.raises(GatewayTokenInvalidError):
        second.verify(token)
