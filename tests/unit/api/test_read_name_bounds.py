"""The roster read behind every resolved name is bounded and best-effort.

A page's names are context. A roster read that stalls must cost the rows their
names, never cost the caller a task, meeting or interrupt response that was
already complete without them, so the read runs under the same deadline its
sibling resolvers do and degrades to naming nobody.
"""

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from synthorg.api import _read_names
from synthorg.api._read_names import agent_name_map, resolved_actor_name
from synthorg.api.state import AppState
from synthorg.config.agent_schema import AgentConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.state import SettingsStateSlice
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


async def _never_returns() -> list[AgentConfig]:
    """Block past any deadline the caller sets.

    Returns:
        Never: the caller's timeout fires first.
    """
    await asyncio.Event().wait()
    return []


async def _raises() -> list[AgentConfig]:
    """Always fail.

    Raises:
        RuntimeError: Always.
    """
    message = "roster unreadable"
    raise RuntimeError(message)


def _state_reading(roster: Callable[[], Awaitable[list[AgentConfig]]]) -> AppState:
    """An app state whose config resolver reads its roster via *roster*.

    A protocol-shaped double rather than a hand-written class: the accessor is
    runtime type-checked against the whole `ConfigResolver` protocol, so a
    partial stand-in raises inside the resolver's own best-effort handler and
    the test passes on an empty map it never earned.

    Returns:
        The composed app state.
    """
    resolver = mock_of[ConfigResolver](get_agents=roster)
    return make_app_state(slices={SettingsStateSlice: {"config_resolver": resolver}})


class TestTheRosterReadIsBounded:
    async def test_a_stalled_roster_read_gives_up_and_names_nobody(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Unbounded, this awaits forever and holds a response that was already
        # complete without the names. The deadline is shortened rather than
        # waited out: what is under test is that one applies at all.
        monkeypatch.setattr(_read_names, "_NAME_READ_TIMEOUT_SECONDS", 0.01)

        async with asyncio.timeout(5):
            names = await agent_name_map(_state_reading(_never_returns))

        assert names == {}

    async def test_a_failed_roster_read_names_nobody(self) -> None:
        assert await agent_name_map(_state_reading(_raises)) == {}

    async def test_a_readable_roster_is_resolved(self) -> None:
        agent = AgentConfig(name="Ada", role="Engineer", department="engineering")

        async def _roster() -> list[AgentConfig]:
            return [agent]

        names = await agent_name_map(_state_reading(_roster))

        # Asked the way a surface asks, rather than by rebuilding the key here.
        assert resolved_actor_name(str(agent.id), names) == "Ada"
