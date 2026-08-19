"""A hire's model is proposed from what the operator has, never invented.

The invariant: every pair offered for a hire came out of the operator's own
configured catalogue, scored by the same matcher that fills out a template
roster, and an org with nothing that fits is TOLD so rather than handed a pair
that does not exist. Nothing here ranks providers or picks a default: the
options are alternatives an operator chooses between.
"""

import pytest

from synthorg.hr.hire_model_proposal import propose_hire_models
from tests._shared.model_binding import (
    TEST_PROVIDER,
    no_provider_catalogue,
    provider_catalogue,
)
from tests.unit.hr.conftest import make_candidate_card

pytestmark = pytest.mark.unit


class TestProposeHireModels:
    async def test_offers_a_pair_from_the_configured_catalogue(self) -> None:
        proposal = await propose_hire_models(
            make_candidate_card(),
            catalogue=provider_catalogue(["example-capable-001"]),
        )
        assert proposal.unmatched_reason is None
        assert proposal.recommended is not None
        assert proposal.recommended.ref.provider == TEST_PROVIDER
        assert proposal.recommended.ref.model_id == "example-capable-001"

    async def test_every_option_names_both_halves(self) -> None:
        # A bare model id names no dispatch target: a provider is a registered
        # connection with its own credentials, endpoint and quota.
        proposal = await propose_hire_models(
            make_candidate_card(),
            catalogue=provider_catalogue(["example-basic-001", "example-expert-001"]),
        )
        assert proposal.options
        assert all(option.ref.is_bound for option in proposal.options)

    async def test_every_configured_model_is_offered_to_override_with(self) -> None:
        # The alternatives are the operator's own catalogue: a second opinion
        # derived from it converges on one pair, and an operator offered three
        # labels for the same model has no choice at all.
        models = ["example-basic-001", "example-capable-001", "example-expert-001"]
        proposal = await propose_hire_models(
            make_candidate_card(), catalogue=provider_catalogue(models)
        )
        assert {option.ref.model_id for option in proposal.options} == set(models)
        assert len({option.option_id for option in proposal.options}) == len(
            proposal.options
        )

    async def test_exactly_one_option_is_recommended(self) -> None:
        proposal = await propose_hire_models(
            make_candidate_card(),
            catalogue=provider_catalogue(["example-basic-001", "example-expert-001"]),
        )
        assert sum(o.recommended for o in proposal.options) == 1

    async def test_the_recommendation_leads_the_list(self) -> None:
        # An operator who reads nothing takes the first option, so the first
        # option has to be the one the matcher actually proposed.
        proposal = await propose_hire_models(
            make_candidate_card(),
            catalogue=provider_catalogue(["example-basic-001", "example-expert-001"]),
        )
        assert proposal.options[0].recommended
        assert proposal.recommended is proposal.options[0]

    async def test_an_unknown_org_profile_still_recommends_something(self) -> None:
        # The profile biases the matcher, so a value it does not know must not
        # cost the hire its whole proposal.
        proposal = await propose_hire_models(
            make_candidate_card(),
            catalogue=provider_catalogue(["example-capable-001"]),
            org_profile="not-a-profile",
        )
        assert proposal.recommended is not None

    async def test_no_catalogue_says_so_rather_than_proposing_nothing(self) -> None:
        proposal = await propose_hire_models(make_candidate_card(), catalogue=None)
        assert proposal.options == ()
        assert proposal.unmatched_reason is not None
        assert proposal.recommended is None

    async def test_no_configured_provider_names_the_remedy(self) -> None:
        proposal = await propose_hire_models(
            make_candidate_card(), catalogue=no_provider_catalogue()
        )
        assert proposal.recommended is None
        assert proposal.unmatched_reason is not None
        assert "Add a provider connection" in proposal.unmatched_reason

    async def test_a_provider_with_no_models_is_a_different_answer(self) -> None:
        # "You have connected nothing" and "nothing you connected fits" want
        # different things from the operator, so they must not share a line.
        proposal = await propose_hire_models(
            make_candidate_card(), catalogue=provider_catalogue([])
        )
        assert proposal.recommended is None
        assert proposal.unmatched_reason is not None
        assert "No configured model satisfies" in proposal.unmatched_reason


class TestOptionIdentity:
    async def test_the_option_id_decodes_back_to_the_pair(self) -> None:
        # The id travels home on the approval, so it has to name the binding
        # rather than index a table that can fall out of step with it.
        from synthorg.settings.model_ref import parse_model_ref

        proposal = await propose_hire_models(
            make_candidate_card(),
            catalogue=provider_catalogue(["example-capable-001"]),
        )
        assert proposal.recommended is not None
        assert parse_model_ref(proposal.recommended.option_id) == (
            proposal.recommended.ref
        )
