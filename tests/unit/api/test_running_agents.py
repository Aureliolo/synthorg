"""The live-run read answers, or says it could not: never the two as one.

Every surface reporting "is the org working" derives it from the task board,
which cannot see a run whose task is not ``IN_PROGRESS``: a planning session is
invisible to the board for its whole life. ``running_agent_ids`` is the other
half of that answer, and the distinction these pin is between an empty set
("nobody is running") and ``None`` ("nobody could be asked"), because a
department card derives utilisation from the count and asserts it is not
degraded.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.api._running_agents import running_agent_ids
from synthorg.budget.currency import CurrencyCode
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_state import AgentRuntimeState, ExecutionStatus
from synthorg.persistence.agent_state_protocol import AgentStateRepository
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import make_app_state, mock_of, sid
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

#: One more than ``collect_all``'s default page, so a drained read is
#: distinguishable from a single-page one.
_PAST_ONE_PAGE = 51


def _state(label: str) -> AgentRuntimeState:
    """Build a live agent-state row for *label*.

    Returns:
        The row, executing.
    """
    return AgentRuntimeState(
        agent_id=NotBlankStr(sid(label)),
        execution_id=NotBlankStr(sid(f"exec-{label}")),
        status=ExecutionStatus.EXECUTING,
        currency=CurrencyCode("EUR"),
        last_activity_at=_NOW,
        started_at=_NOW,
    )


class TestItAnswersFromTheLiveRows:
    async def test_it_names_every_agent_holding_a_run(self) -> None:
        backend = FakePersistenceBackend()
        for label in ("alpha", "beta"):
            await backend.agent_states.save(_state(label))
        app_state = make_app_state(persistence=backend)

        assert await running_agent_ids(app_state) == frozenset(
            {sid("alpha"), sid("beta")}
        )

    async def test_nobody_running_is_an_empty_set_not_none(self) -> None:
        # The distinction the whole return type exists for: an empty set is an
        # answer, and a caller reports it as one.
        app_state = make_app_state(persistence=FakePersistenceBackend())

        assert await running_agent_ids(app_state) == frozenset()

    async def test_it_reads_past_the_first_page(self) -> None:
        # Paged rather than drained, this counted to the page limit and
        # reported the truncation as the number of agents working.
        backend = FakePersistenceBackend()
        for index in range(_PAST_ONE_PAGE):
            await backend.agent_states.save(_state(f"agent-{index:03d}"))
        app_state = make_app_state(persistence=backend)

        running = await running_agent_ids(app_state)

        assert running is not None
        assert len(running) == _PAST_ONE_PAGE


class TestItSaysWhenItCouldNotAsk:
    async def test_unconnected_persistence_answers_none(self) -> None:
        # A deployment still coming up has nothing to read, which is not the
        # same claim as nobody working.
        assert await running_agent_ids(make_app_state()) is None

    async def test_a_failing_query_answers_none(self) -> None:
        states = mock_of[AgentStateRepository](
            get_active=AsyncMock(
                spec=AgentStateRepository.get_active,
                side_effect=QueryError("the agent_states table could not be read"),
            )
        )
        app_state = make_app_state(
            persistence=mock_of[PersistenceBackend](agent_states=states)
        )

        assert await running_agent_ids(app_state) is None
