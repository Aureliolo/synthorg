"""Unit tests for ``CandidatePoolFilter`` implementations."""

import pytest

from synthorg.engine.assignment.models import AssignmentRequest
from synthorg.engine.assignment.pool_filter_protocol import (
    CandidatePoolFilter,
    PoolFilterResult,
)
from synthorg.engine.assignment.pool_filters import IdentityPoolFilter

from .conftest import make_assignment_agent, make_assignment_task

pytestmark = pytest.mark.unit


class TestPoolFilterResult:
    """``PoolFilterResult`` invariants."""

    def test_non_empty_result_has_no_reason(self) -> None:
        agent = make_assignment_agent("dev-1")
        result = PoolFilterResult(agents=(agent,))
        assert result.agents == (agent,)
        assert result.reason is None

    def test_empty_result_can_carry_reason(self) -> None:
        result = PoolFilterResult(agents=(), reason="no eligible pool")
        assert result.agents == ()
        assert result.reason == "no eligible pool"

    def test_is_frozen(self) -> None:
        agent = make_assignment_agent("dev-1")
        result = PoolFilterResult(agents=(agent,))
        with pytest.raises(AttributeError):
            result.agents = ()  # type: ignore[misc]


class TestIdentityPoolFilter:
    """``IdentityPoolFilter`` is the no-op default."""

    def test_implements_protocol(self) -> None:
        assert isinstance(IdentityPoolFilter(), CandidatePoolFilter)

    def test_name(self) -> None:
        assert IdentityPoolFilter().name == "identity"

    def test_returns_pool_unchanged(self) -> None:
        a1 = make_assignment_agent("dev-1")
        a2 = make_assignment_agent("dev-2")
        request = AssignmentRequest(
            task=make_assignment_task(),
            available_agents=(a1, a2),
        )
        result = IdentityPoolFilter().filter(request)
        assert result.agents == (a1, a2)
        assert result.reason is None

    def test_does_not_mutate_request(self) -> None:
        a1 = make_assignment_agent("dev-1")
        request = AssignmentRequest(
            task=make_assignment_task(),
            available_agents=(a1,),
        )
        original_agents = request.available_agents
        IdentityPoolFilter().filter(request)
        assert request.available_agents is original_agents
