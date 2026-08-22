# module-kind: tests
"""One resolver answers how many tokens a response may spend."""

from datetime import date
from uuid import uuid4

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.engine._agent_engine_run import AgentEngineRunMixin
from synthorg.engine.response_budget import (
    DEFAULT_AGENT_MAX_RESPONSE_TOKENS,
    resolve_response_tokens,
)
from synthorg.providers.models import CompletionConfig
from synthorg.settings.resolver import ConfigResolver
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _resolver(value: int | None = None, *, fail: bool = False) -> ConfigResolver:
    """Build a resolver double answering one int, or failing.

    Specced on the concrete resolver rather than on its protocol because the
    engine mixin declares the concrete type: a protocol-typed double is what
    the resolver ladder consumes, but not what the attribute the fold reads
    is annotated as.

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

    double: ConfigResolver = mock_of[ConfigResolver](get_int=_get_int)
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


class TestTheCeilingIsAlwaysUsable:
    """Every source is constrained positive, and none is checked at the use.

    A stored value can outlive the constraint that admitted it, and a zero does
    not fail: it asks the driver for no output at all, which reads downstream
    exactly like a model that answered nothing.
    """

    @pytest.mark.parametrize("stored", [0, -1])
    async def test_a_non_positive_setting_falls_back(self, stored: int) -> None:
        resolved = await resolve_response_tokens(_resolver(stored), _identity(None))

        assert resolved == DEFAULT_AGENT_MAX_RESPONSE_TOKENS


class _Folder(AgentEngineRunMixin):
    """The fold under test, with only the collaborator it reads."""

    def __init__(self, resolver: ConfigResolver | None) -> None:
        self._config_resolver = resolver


class TestTheFoldThatCommitsTheCeiling:
    """The ladder is only worth anything where a dispatch actually reads it.

    ``resolve_response_tokens`` answering correctly proves nothing on its own:
    the fold decides whether it is consulted at all, and it runs on every
    dispatch whose agent states no ceiling, which is every agent by default.
    """

    async def test_an_unset_binding_is_resolved_before_the_binding_commits(
        self,
    ) -> None:
        folded = await _Folder(_resolver(4242))._fold_response_budget(
            None, _identity(None)
        )

        assert folded.max_tokens == 4242

    async def test_a_config_that_already_carries_a_ceiling_is_left_alone(
        self,
    ) -> None:
        """An earlier fold's explicit value is a decision, not an absence."""
        carried = CompletionConfig(temperature=0.3, max_tokens=777)

        folded = await _Folder(_resolver(4242))._fold_response_budget(
            carried, _identity(None)
        )

        assert folded.max_tokens == 777

    async def test_the_folded_config_never_leaves_the_ceiling_unset(self) -> None:
        """`None` past this point reaches the driver as no ceiling at all."""
        folded = await _Folder(None)._fold_response_budget(None, _identity(None))

        assert folded.max_tokens is not None

    async def test_the_agents_temperature_survives_the_fold(self) -> None:
        """The fold settles one field; it must not mint a fresh config."""
        carried = CompletionConfig(temperature=0.11)

        folded = await _Folder(_resolver(4242))._fold_response_budget(
            carried, _identity(None)
        )

        assert folded.temperature == pytest.approx(0.11)
