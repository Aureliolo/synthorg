"""An approval an operator must decide carries what the decision turns on.

The invariant under test is not "the description mentions a model". It is
that every fact the org commits to by approving a hire is legible on the item
itself, and above all that the binding the hire would be instantiated against
is stated either way: named when bound, and named as ABSENT when not, because
an unset ``hr.new_hire_model`` makes approval a decision the system then
refuses to carry out.
"""

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.agent import ModelConfig
from synthorg.hr.hiring_candidates import build_hire_approval_item, hire_decision_brief
from synthorg.hr.hiring_service import HiringService
from synthorg.hr.registry import AgentRegistryService
from tests._shared import as_uuid, sid
from tests._shared.model_binding import TEST_PROVIDER, bound_ref, model_ref_resolver
from tests.unit.hr.conftest import make_candidate_card, make_hiring_request

_BOUND = ModelConfig(provider=TEST_PROVIDER, model_id="example-capable-001")


@pytest.mark.unit
class TestHireDecisionBrief:
    """What the operator reads before approving a hire."""

    def test_names_the_team_the_agent_joins(self) -> None:
        brief = hire_decision_brief(
            make_hiring_request(),
            make_candidate_card(department="engineering"),
            bound_model=_BOUND,
        )
        assert "engineering" in brief

    def test_names_what_the_candidate_claims_it_can_do(self) -> None:
        brief = hire_decision_brief(
            make_hiring_request(),
            make_candidate_card(skills=("python", "databases")),
            bound_model=_BOUND,
        )
        assert "python" in brief
        assert "databases" in brief

    def test_names_the_recurring_cost_being_committed(self) -> None:
        brief = hire_decision_brief(
            make_hiring_request(),
            make_candidate_card(estimated_monthly_cost=50.0),
            bound_model=_BOUND,
        )
        assert "50" in brief

    def test_names_both_halves_of_the_bound_pair(self) -> None:
        brief = hire_decision_brief(
            make_hiring_request(),
            make_candidate_card(),
            bound_model=_BOUND,
        )
        assert _BOUND.model_id in brief
        assert _BOUND.provider in brief

    def test_says_so_when_nothing_is_bound(self) -> None:
        brief = hire_decision_brief(
            make_hiring_request(),
            make_candidate_card(),
            bound_model=None,
        )
        assert "hr.new_hire_model" in brief
        assert "NOT BOUND" in brief

    def test_keeps_the_reason_the_hire_was_asked_for(self) -> None:
        brief = hire_decision_brief(
            make_hiring_request(reason="Completion Reviewer is unstaffed"),
            make_candidate_card(),
            bound_model=_BOUND,
        )
        assert "Completion Reviewer is unstaffed" in brief


@pytest.mark.unit
class TestHireApprovalItemCarriesTheBrief:
    """The item stored for a human to decide is the brief, not a bare title."""

    def test_description_states_the_binding(self) -> None:
        item = build_hire_approval_item(
            make_hiring_request(),
            make_candidate_card(department="engineering"),
            candidate_id=sid("candidate-1"),
            approval_id=str(as_uuid("approval-1")),
            bound_model=_BOUND,
        )
        assert _BOUND.model_id in item.description
        assert "engineering" in item.description

    def test_description_states_an_absent_binding(self) -> None:
        item = build_hire_approval_item(
            make_hiring_request(),
            make_candidate_card(),
            candidate_id=sid("candidate-1"),
            approval_id=str(as_uuid("approval-1")),
            bound_model=None,
        )
        assert "NOT BOUND" in item.description


@pytest.mark.unit
class TestSubmittedApprovalReadsTheLiveBinding:
    """The service reads the pair, so the card cannot drift from the setting."""

    async def test_stored_item_names_the_bound_pair(
        self,
        registry: AgentRegistryService,
    ) -> None:
        store = ApprovalStore()
        service = HiringService(
            registry=registry,
            approval_store=store,
            config_resolver=model_ref_resolver(default=bound_ref("example-expert-001")),
        )
        request = await service.create_request(
            requested_by="cto",
            department="engineering",
            role="developer",
            reason="Need capacity",
        )
        with_candidate = await service.generate_candidate(request)
        submitted = await service.submit_for_approval(
            with_candidate,
            str(with_candidate.candidates[0].id),
        )
        assert submitted.approval_id is not None
        item = await store.get(submitted.approval_id)
        assert item is not None
        assert "example-expert-001" in item.description

    async def test_stored_item_warns_when_the_pair_is_unset(
        self,
        registry: AgentRegistryService,
    ) -> None:
        store = ApprovalStore()
        service = HiringService(
            registry=registry,
            approval_store=store,
            config_resolver=model_ref_resolver(default=""),
        )
        request = await service.create_request(
            requested_by="cto",
            department="engineering",
            role="developer",
            reason="Need capacity",
        )
        with_candidate = await service.generate_candidate(request)
        submitted = await service.submit_for_approval(
            with_candidate,
            str(with_candidate.candidates[0].id),
        )
        assert submitted.approval_id is not None
        item = await store.get(submitted.approval_id)
        assert item is not None
        assert "NOT BOUND" in item.description
