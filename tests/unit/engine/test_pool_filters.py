"""Unit tests for ``CandidatePoolFilter`` implementations."""

from typing import Any

import pytest

from synthorg.engine.assignment.models import AssignmentRequest
from synthorg.engine.assignment.pool_filter_protocol import (
    CandidatePoolFilter,
    PoolFilterResult,
)
from synthorg.engine.assignment.pool_filters import (
    HierarchicalPoolFilter,
    IdentityPoolFilter,
)

from .conftest import make_assignment_agent, make_assignment_task

pytestmark = pytest.mark.unit


class _StubHierarchy:
    """Stub HierarchyResolver for unit-isolating HierarchicalPoolFilter."""

    def __init__(
        self,
        *,
        direct_reports: dict[str, list[str]] | None = None,
        supervisors: dict[str, str | None] | None = None,
        subordinates: dict[str, set[str]] | None = None,
        raise_on: str | None = None,
    ) -> None:
        self._direct_reports = direct_reports or {}
        self._supervisors = supervisors or {}
        self._subordinates = subordinates or {}
        self._raise_on = raise_on

    def get_direct_reports(self, name: str) -> list[str]:
        if self._raise_on == "get_direct_reports":
            msg = "stub hierarchy backing-store error"
            raise RuntimeError(msg)
        return self._direct_reports.get(name, [])

    def get_supervisor(self, name: str) -> str | None:
        return self._supervisors.get(name)

    def is_subordinate(self, supervisor: str, candidate: str) -> bool:
        if self._raise_on == "is_subordinate":
            msg = "stub hierarchy backing-store error"
            raise RuntimeError(msg)
        return candidate in self._subordinates.get(supervisor, set())


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

    def test_empty_agents_without_reason_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty agents requires"):
            PoolFilterResult(agents=())

    def test_non_empty_agents_with_reason_rejected(self) -> None:
        agent = make_assignment_agent("dev-1")
        with pytest.raises(ValueError, match="must not carry a reason"):
            PoolFilterResult(agents=(agent,), reason="oops")


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
        assert result.rewrite_success_reason is None

    def test_does_not_mutate_request(self) -> None:
        a1 = make_assignment_agent("dev-1")
        request = AssignmentRequest(
            task=make_assignment_task(),
            available_agents=(a1,),
        )
        IdentityPoolFilter().filter(request)
        # Request is frozen; the field still equals what we passed in.
        assert request.available_agents == (a1,)


class TestHierarchicalPoolFilter:
    """Direct unit tests for ``HierarchicalPoolFilter.filter()``."""

    def _request(
        self,
        *,
        agent_names: list[str],
        created_by: str,
        delegation_chain: tuple[str, ...] = (),
    ) -> AssignmentRequest:
        return AssignmentRequest(
            task=make_assignment_task(
                created_by=created_by,
                delegation_chain=delegation_chain,
            ),
            available_agents=tuple(make_assignment_agent(n) for n in agent_names),
        )

    def test_implements_protocol(self) -> None:
        flt = HierarchicalPoolFilter(_StubHierarchy())  # type: ignore[arg-type]
        assert isinstance(flt, CandidatePoolFilter)

    def test_name(self) -> None:
        flt = HierarchicalPoolFilter(_StubHierarchy())  # type: ignore[arg-type]
        assert flt.name == "hierarchical"

    def test_unknown_delegator_returns_empty_with_reason(self) -> None:
        # Stub hierarchy knows nothing; ``_is_known_delegator`` returns False.
        hierarchy: Any = _StubHierarchy()
        flt = HierarchicalPoolFilter(hierarchy)
        request = self._request(agent_names=["dev-1"], created_by="ghost")
        result = flt.filter(request)
        assert result.agents == ()
        assert result.reason is not None
        assert "ghost" in result.reason
        assert "not found in hierarchy" in result.reason

    def test_no_subordinates_returns_empty_with_reason(self) -> None:
        # Manager exists in hierarchy (has reports) but none are in the pool.
        hierarchy: Any = _StubHierarchy(
            direct_reports={"manager": ["someone-not-in-pool"]},
        )
        flt = HierarchicalPoolFilter(hierarchy)
        request = self._request(agent_names=["dev-1"], created_by="manager")
        result = flt.filter(request)
        assert result.agents == ()
        assert result.reason is not None
        assert "No subordinates of 'manager'" in result.reason

    def test_direct_report_selected(self) -> None:
        hierarchy: Any = _StubHierarchy(
            direct_reports={"manager": ["dev-1", "dev-2"]},
        )
        flt = HierarchicalPoolFilter(hierarchy)
        request = self._request(agent_names=["dev-1", "dev-3"], created_by="manager")
        result = flt.filter(request)
        # Only dev-1 is a direct report and in the pool.
        names = [a.name for a in result.agents]
        assert names == ["dev-1"]
        # Success path: reason is None, rewriter is set.
        assert result.reason is None
        assert result.rewrite_success_reason is not None

    def test_transitive_subordinate_fallback(self) -> None:
        # No direct reports of "ceo" are in the pool -> transitive lookup.
        hierarchy: Any = _StubHierarchy(
            direct_reports={"ceo": ["vp"]},
            subordinates={"ceo": {"dev-1"}},
        )
        flt = HierarchicalPoolFilter(hierarchy)
        request = self._request(agent_names=["dev-1"], created_by="ceo")
        result = flt.filter(request)
        names = [a.name for a in result.agents]
        assert names == ["dev-1"]

    def test_delegation_chain_takes_precedence_over_created_by(self) -> None:
        # delegation_chain[-1] should be used as the delegator.
        hierarchy: Any = _StubHierarchy(
            direct_reports={"lead": ["dev-1"], "ceo": ["lead"]},
        )
        flt = HierarchicalPoolFilter(hierarchy)
        request = self._request(
            agent_names=["dev-1"],
            created_by="ceo",
            delegation_chain=("ceo", "lead"),
        )
        result = flt.filter(request)
        # Should use "lead" (the tail of delegation_chain), find dev-1.
        names = [a.name for a in result.agents]
        assert names == ["dev-1"]

    def test_known_via_supervisor_only(self) -> None:
        # An agent with no direct reports but a supervisor IS known.
        # Used so the empty-direct-reports leaf delegators still get
        # the no-subordinates reason rather than the unknown-delegator
        # reason.
        hierarchy: Any = _StubHierarchy(
            direct_reports={},  # no reports
            supervisors={"leaf": "manager"},  # supervised
        )
        flt = HierarchicalPoolFilter(hierarchy)
        request = self._request(agent_names=["dev-1"], created_by="leaf")
        result = flt.filter(request)
        # Known but no subordinates -> "No subordinates" reason.
        assert "No subordinates of 'leaf'" in (result.reason or "")

    def test_hierarchy_lookup_failure_in_is_known_returns_empty(self) -> None:
        hierarchy: Any = _StubHierarchy(raise_on="get_direct_reports")
        flt = HierarchicalPoolFilter(hierarchy)
        request = self._request(agent_names=["dev-1"], created_by="manager")
        result = flt.filter(request)
        assert result.agents == ()
        assert "Hierarchy lookup failed" in (result.reason or "")
        assert "is_known_delegator" in (result.reason or "")
        assert "RuntimeError" in (result.reason or "")

    def test_hierarchy_lookup_failure_in_filter_returns_empty(self) -> None:
        # Known delegator passes the first lookup, but the transitive
        # fallback raises (direct_reports succeeds with no match, then
        # is_subordinate raises during the transitive scan).
        hierarchy = _StubHierarchy(
            direct_reports={"manager": ["someone-else"]},
            raise_on="is_subordinate",
        )
        flt = HierarchicalPoolFilter(hierarchy)  # type: ignore[arg-type]
        request = self._request(agent_names=["dev-1"], created_by="manager")
        result = flt.filter(request)
        assert result.agents == ()
        assert "Hierarchy lookup failed" in (result.reason or "")
        assert "filter_by_hierarchy" in (result.reason or "")

    def test_rewrite_success_reason_includes_delegator(self) -> None:
        hierarchy: Any = _StubHierarchy(
            direct_reports={"manager": ["dev-1"]},
        )
        flt = HierarchicalPoolFilter(hierarchy)
        request = self._request(agent_names=["dev-1"], created_by="manager")
        result = flt.filter(request)
        assert result.rewrite_success_reason is not None
        # Build a fake selected candidate and call the rewriter.
        from synthorg.engine.assignment.models import AssignmentCandidate

        selected = AssignmentCandidate(
            agent_identity=make_assignment_agent("dev-1"),
            score=0.85,
            reason="test",
        )
        rewritten = result.rewrite_success_reason(selected)
        assert "Delegated from 'manager' to" in rewritten
        assert "'dev-1'" in rewritten
        assert "0.85" in rewritten
