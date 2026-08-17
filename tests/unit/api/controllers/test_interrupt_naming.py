"""An interrupt card says who is asking, by name.

A parked agent is putting a question to the operator, so the row has to name
them. ``agent_name`` is resolved once per page at the read boundary; ``None`` is
the honest answer when the roster does not cover them, and the surface supplies
its own words for that. It is never the key.
"""

from datetime import UTC, datetime

import pytest

from synthorg.communication.event_stream.interrupt import (
    Interrupt,
    InterruptStore,
    InterruptType,
)
from synthorg.config.agent_schema import AgentConfig
from synthorg.core.types import stable_agent_id
from synthorg.settings.state import config_resolver_of
from tests._shared import LoopAsyncClient, sid
from tests.unit.api.conftest import make_auth_headers

pytestmark = pytest.mark.unit

_READ_HEADERS = make_auth_headers("observer")
_BASE = "/api/v1/interrupts"

_ASKER_NAME = "Ada"
# `AgentConfig` derives its id from the name on every construction, so the
# interrupt has to reference the id the roster will actually produce.
_ASKER = str(stable_agent_id(_ASKER_NAME))
_STRANGER = sid("interrupt-naming-stranger")


def _agent(name: str) -> AgentConfig:
    """Build a roster agent.

    Returns:
        The agent config.
    """
    return AgentConfig(
        name=name,
        role="Engineer",
        department="engineering",
        model={"provider": "test-provider", "model_id": "test-basic-001"},
    )


def _interrupt(interrupt_id: str, agent_id: str) -> Interrupt:
    """Build a pending tool-approval interrupt.

    Returns:
        The interrupt.
    """
    return Interrupt(
        id=interrupt_id,
        type=InterruptType.TOOL_APPROVAL,
        session_id="s1",
        agent_id=agent_id,
        created_at=datetime(2026, 4, 13, tzinfo=UTC),
        timeout_seconds=300.0,
        tool_name="deploy",
    )


@pytest.fixture
def _one_agent_roster(
    async_test_client: LoopAsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the roster to one named agent, so "unknown" means the other one."""
    roster = (_agent(_ASKER_NAME),)

    async def get_agents() -> tuple[AgentConfig, ...]:
        return roster

    monkeypatch.setattr(
        config_resolver_of(async_test_client.app.state.app_state),
        "get_agents",
        get_agents,
    )


@pytest.mark.usefixtures("_one_agent_roster")
class TestTheAskerIsNamed:
    """Resolved from the roster per page, never carried on the stored row."""

    async def test_a_roster_agent_is_named(
        self,
        async_test_client: LoopAsyncClient,
        interrupt_store: InterruptStore,
    ) -> None:
        await interrupt_store.create(_interrupt("int-named", _ASKER))

        resp = await async_test_client.get(_BASE, headers=_READ_HEADERS)

        (row,) = [r for r in resp.json()["data"] if r["id"] == "int-named"]
        assert row["agent_name"] == "Ada"
        # The reference still travels: the card links by it.
        assert row["agent_id"] == _ASKER

    async def test_an_agent_the_roster_does_not_cover_is_unnamed(
        self,
        async_test_client: LoopAsyncClient,
        interrupt_store: InterruptStore,
    ) -> None:
        """``None``, never the key: a fallback would print the UUID."""
        await interrupt_store.create(_interrupt("int-stranger", _STRANGER))

        resp = await async_test_client.get(_BASE, headers=_READ_HEADERS)

        (row,) = [r for r in resp.json()["data"] if r["id"] == "int-stranger"]
        assert row["agent_name"] is None
        assert row["agent_id"] == _STRANGER


class TestNothingPendingReadsNoRoster:
    """The steady state of this poll is empty, and it should cost nothing."""

    async def test_an_empty_page_skips_the_roster_read(
        self,
        async_test_client: LoopAsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        reads = 0

        async def counting_get_agents() -> tuple[AgentConfig, ...]:
            nonlocal reads
            reads += 1
            return ()

        monkeypatch.setattr(
            config_resolver_of(async_test_client.app.state.app_state),
            "get_agents",
            counting_get_agents,
        )

        resp = await async_test_client.get(_BASE, headers=_READ_HEADERS)

        assert resp.json()["data"] == []
        assert reads == 0
