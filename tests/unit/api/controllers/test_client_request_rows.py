"""The client name a request row carries, and what happens when it cannot.

Every lifecycle handler resolves the name AFTER its write has landed and its
WebSocket event has gone out, so a resolver that raises turns a request that
already exists into a 500. The operator retries and files the work twice. A
name is context; it can never be what fails the response carrying it.
"""

import pytest

from synthorg.api.controllers.requests._rows import ClientRequestRow, client_names
from synthorg.api.state import AppState
from synthorg.client.models import ClientProfile, ClientRequest, TaskRequirement
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.client.state import ClientStateSlice
from synthorg.core.types import NotBlankStr
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit

_REQUIREMENT = TaskRequirement(
    title=NotBlankStr("Ship the login page"),
    description=NotBlankStr("The one an operator reads"),
)


class _RaisingPool:
    """A client pool whose read fails, the way an unreachable one does."""

    async def list_profiles(self) -> list[ClientProfile]:
        """Always fail.

        Raises:
            RuntimeError: Always.
        """
        message = "pool unreachable"
        raise RuntimeError(message)


class _CriticalPool:
    """A pool whose read fails in a way nothing may swallow."""

    async def list_profiles(self) -> list[ClientProfile]:
        """Always fail critically.

        Raises:
            MemoryError: Always.
        """
        raise MemoryError


def _state_with(pool: object) -> AppState:
    """An app state whose simulation pool is *pool*.

    Returns:
        The composed app state.
    """
    simulation = mock_of[ClientSimulationState](pool=pool)
    return make_app_state(slices={ClientStateSlice: {"simulation_state": simulation}})


class TestAnUnreadablePoolNamesNobody:
    async def test_a_failed_read_yields_an_empty_map(self) -> None:
        names = await client_names(_state_with(_RaisingPool()))

        assert names == {}

    async def test_a_critical_failure_is_not_swallowed(self) -> None:
        with pytest.raises(MemoryError):
            await client_names(_state_with(_CriticalPool()))


class TestTheRowWordsAnUnknownClientItself:
    def test_an_unresolved_client_answers_none_not_the_key(self) -> None:
        request = ClientRequest(
            request_id="req-1",
            client_id="client-gone",
            requirement=_REQUIREMENT,
        )

        row = ClientRequestRow.of(request, {})

        # None, never the key: the surface prints its own words for it.
        assert row.client_name is None
        assert row.client_id == "client-gone"

    def test_a_resolved_client_carries_its_name(self) -> None:
        request = ClientRequest(
            request_id="req-1",
            client_id="acme",
            requirement=_REQUIREMENT,
        )

        row = ClientRequestRow.of(request, {"acme": "Acme Industries"})

        assert row.client_name == NotBlankStr("Acme Industries")
