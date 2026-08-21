# module-kind: tests
"""One resolver answers how many tokens a response may spend."""

from datetime import date
from uuid import uuid4

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.engine.response_budget import (
    DEFAULT_AGENT_MAX_RESPONSE_TOKENS,
    resolve_response_tokens,
)
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _resolver(
    value: int | None = None, *, fail: bool = False
) -> ConfigResolverProtocol:
    """Build a resolver double answering one int, or failing.

    Built with ``mock_of`` rather than by hand: typeguard checks the WHOLE
    protocol on a fake at runtime, so a class implementing only the method
    under test is rejected for the methods it does not have.

    Returns:
        The double.
    """

    async def _get_int(namespace: str, key: str) -> int:
        assert namespace == "engine"
        assert key == "agent_max_response_tokens"
        if fail:
            msg = "settings unavailable"
            raise RuntimeError(msg)
        assert value is not None
        return value

    double: ConfigResolverProtocol = mock_of[ConfigResolverProtocol](get_int=_get_int)
    return double


def _identity(max_tokens: int | None) -> AgentIdentity:
    """Build an identity whose binding may or may not state a ceiling.

    Returns:
        The identity.
    """
    return AgentIdentity(
        id=uuid4(),
        name="Builder",
        role="Developer",
        department="Engineering",
        model=ModelConfig(
            provider="example-provider",
            model_id="example-capable-001",
            max_tokens=max_tokens,
        ),
        hiring_date=date(2026, 1, 1),
    )


async def test_the_agents_own_ceiling_wins() -> None:
    """An operator who set a value on the agent keeps it."""
    resolved = await resolve_response_tokens(_resolver(999), _identity(1234))

    assert resolved == 1234


async def test_unset_defers_to_the_setting() -> None:
    """None is what distinguishes 'unset' from 'deliberately small'."""
    resolved = await resolve_response_tokens(_resolver(9999), _identity(None))

    assert resolved == 9999


async def test_no_resolver_falls_back_to_the_shipped_default() -> None:
    """A harness with no settings store still gets a workable ceiling."""
    resolved = await resolve_response_tokens(None, _identity(None))

    assert resolved == DEFAULT_AGENT_MAX_RESPONSE_TOKENS


async def test_settings_outage_falls_back_rather_than_raising() -> None:
    """A transient settings failure must not wedge a dispatch."""
    resolved = await resolve_response_tokens(_resolver(fail=True), _identity(None))

    assert resolved == DEFAULT_AGENT_MAX_RESPONSE_TOKENS


async def test_the_default_is_large_enough_for_a_reasoning_turn() -> None:
    """Below roughly 8k a reasoning model can emit no tool call at all.

    That is the failure this default exists to prevent, and it is silent: the
    loop reads a turn with no tool call as a finished session, so the run is
    recorded as work completed rather than as a truncation.
    """
    assert DEFAULT_AGENT_MAX_RESPONSE_TOKENS >= 8192
