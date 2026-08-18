"""An approval an operator must decide carries what the decision turns on.

The invariant is not "the description mentions a model". It is that every fact
the org commits to by approving a hire is legible on the item itself, and above
all that the pair the agent would be bound to is stated: proposed from the
models the operator actually configured, offered as a fork they can override
without leaving the approval, and named as ABSENT when nothing is proposable,
because approving then refuses the hire.
"""

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.hr.hire_model_proposal import HireModelOption, HireModelProposal
from synthorg.hr.hiring_candidates import build_hire_approval_item, hire_decision_brief
from synthorg.hr.hiring_service import HiringService
from synthorg.hr.registry import AgentRegistryService
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from tests._shared import as_uuid, sid
from tests._shared.model_binding import TEST_PROVIDER
from tests.unit.hr.conftest import make_candidate_card, make_hiring_request

_CAPABLE = ModelRef(provider=TEST_PROVIDER, model_id="example-capable-001")
_EXPERT = ModelRef(provider=TEST_PROVIDER, model_id="example-expert-001")


def _option(ref: ModelRef, *, recommended: bool) -> HireModelOption:
    return HireModelOption(ref=ref, capability="capable", recommended=recommended)


def _proposal(*options: HireModelOption) -> HireModelProposal:
    return HireModelProposal(options=options)


_ONE = _proposal(_option(_CAPABLE, recommended=True))
_TWO = _proposal(
    _option(_CAPABLE, recommended=True),
    _option(_EXPERT, recommended=False),
)
_NONE = HireModelProposal(
    unmatched_reason="No configured model satisfies what this role needs."
)


@pytest.mark.unit
class TestHireDecisionBrief:
    """What the operator reads before approving a hire."""

    def test_names_the_team_the_agent_joins(self) -> None:
        brief = hire_decision_brief(
            make_hiring_request(),
            make_candidate_card(department="engineering"),
            proposal=_ONE,
        )
        assert "engineering" in brief

    def test_names_what_the_candidate_claims_it_can_do(self) -> None:
        brief = hire_decision_brief(
            make_hiring_request(),
            make_candidate_card(skills=("python", "databases")),
            proposal=_ONE,
        )
        assert "python" in brief
        assert "databases" in brief

    def test_names_the_recurring_cost_being_committed(self) -> None:
        brief = hire_decision_brief(
            make_hiring_request(),
            make_candidate_card(estimated_monthly_cost=50.0),
            proposal=_ONE,
        )
        assert "50" in brief

    def test_names_both_halves_of_the_proposed_pair(self) -> None:
        brief = hire_decision_brief(
            make_hiring_request(), make_candidate_card(), proposal=_ONE
        )
        assert _CAPABLE.model_id in brief
        assert _CAPABLE.provider in brief

    def test_says_so_when_nothing_is_proposable(self) -> None:
        brief = hire_decision_brief(
            make_hiring_request(), make_candidate_card(), proposal=_NONE
        )
        assert "NONE AVAILABLE" in brief
        assert "No configured model satisfies" in brief

    def test_points_at_the_alternatives_when_there_are_some(self) -> None:
        brief = hire_decision_brief(
            make_hiring_request(), make_candidate_card(), proposal=_TWO
        )
        assert "Pick a different option" in brief

    def test_keeps_the_reason_the_hire_was_asked_for(self) -> None:
        brief = hire_decision_brief(
            make_hiring_request(reason="Completion Reviewer is unstaffed"),
            make_candidate_card(),
            proposal=_ONE,
        )
        assert "Completion Reviewer is unstaffed" in brief


@pytest.mark.unit
class TestHireApprovalItemCarriesTheFork:
    """The item stored for a human is the decision, not a bare title."""

    def test_description_states_the_binding(self) -> None:
        item = build_hire_approval_item(
            make_hiring_request(),
            make_candidate_card(department="engineering"),
            candidate_id=sid("candidate-1"),
            approval_id=str(as_uuid("approval-1")),
            proposal=_ONE,
        )
        assert _CAPABLE.model_id in item.description
        assert "engineering" in item.description

    def test_a_single_pair_offers_no_fork(self) -> None:
        # One option is not a choice, and a package carrying one would add an
        # empty decision panel to the drawer for an operator who cannot act
        # on it.
        item = build_hire_approval_item(
            make_hiring_request(),
            make_candidate_card(),
            candidate_id=sid("candidate-1"),
            approval_id=str(as_uuid("approval-1")),
            proposal=_ONE,
        )
        assert item.evidence_package is None

    def test_several_pairs_become_an_overridable_fork(self) -> None:
        item = build_hire_approval_item(
            make_hiring_request(),
            make_candidate_card(),
            candidate_id=sid("candidate-1"),
            approval_id=str(as_uuid("approval-1")),
            proposal=_TWO,
        )
        assert item.evidence_package is not None
        options = item.evidence_package.options
        # The id IS the pair, so the operator's pick needs no lookup table.
        assert [o.id for o in options] == [
            serialize_model_ref(_CAPABLE),
            serialize_model_ref(_EXPERT),
        ]
        assert [o.recommended for o in options] == [True, False]

    def test_description_states_an_absent_binding(self) -> None:
        item = build_hire_approval_item(
            make_hiring_request(),
            make_candidate_card(),
            candidate_id=sid("candidate-1"),
            approval_id=str(as_uuid("approval-1")),
            proposal=_NONE,
        )
        assert "NONE AVAILABLE" in item.description
        assert item.evidence_package is None


@pytest.mark.unit
class TestSubmittedApprovalRecordsTheRecommendation:
    """The request carries a binding, so approving without a pick still hires."""

    async def test_no_catalogue_leaves_the_request_unbound_and_says_why(
        self,
        registry: AgentRegistryService,
    ) -> None:
        store = ApprovalStore()
        service = HiringService(registry=registry, approval_store=store)
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
        assert submitted.bound_model_ref is None
        assert submitted.approval_id is not None
        item = await store.get(submitted.approval_id)
        assert item is not None
        assert "NONE AVAILABLE" in item.description
