"""The recommendations endpoint only offers refs the wizard can select.

Recommendations are derived from the persisted roster while the candidate
list is built from the live provider configs, so the two can disagree: an
agent still assigned a provider or model that has since been removed would
otherwise prefill a ref absent from the options. The picker preselects by
string identity, so such a value renders as an empty select that is
nonetheless holding a value -- the same class of unusable prefill this
endpoint exists to avoid.
"""

from unittest.mock import AsyncMock, patch

import pytest
from litestar.datastructures import State

from synthorg.api.controllers.setup._embedder_setup import collect_provider_models
from synthorg.api.controllers.setup.company import SetupCompanyController
from synthorg.api.controllers.setup_agents import get_existing_agents
from synthorg.api.controllers.setup_model_recommendations import (
    SetupModelRecommendationsResponse,
)
from synthorg.api.dto import ApiResponse
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from synthorg.settings.service import SettingsService
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit

_MODULE = "synthorg.api.controllers.setup.company"


def _controller() -> SetupCompanyController:
    """Build a route-free controller instance for direct handler calls.

    Returns:
        An uninitialised controller; the handler under test reads only state.
    """
    return object.__new__(SetupCompanyController)


def _ref(provider: str, model_id: str) -> str:
    """The canonical stored form for a provider-bound assignment.

    Returns:
        The serialised model reference.
    """
    return serialize_model_ref(ModelRef(provider=provider, model_id=model_id))


def _agent(provider: str, model_id: str, tier: str = "large") -> dict[str, object]:
    """A roster agent carrying a fully bound model assignment.

    Returns:
        The agent dict shape ``get_existing_agents`` returns.
    """
    return {"tier": tier, "model": {"provider": provider, "model_id": model_id}}


async def _recommendations(
    agents: list[dict[str, object]],
    provider_models: tuple[tuple[str, str], ...],
) -> SetupModelRecommendationsResponse:
    """Call the endpoint with a controlled roster and provider catalogue.

    Args:
        agents: Roster agents the settings service reports.
        provider_models: ``(provider, model_id)`` pairs the live configs serve.

    Returns:
        The response payload.
    """
    app_state = make_app_state(settings_service=mock_of[SettingsService]())
    with (
        patch(
            f"{_MODULE}.get_existing_agents",
            AsyncMock(spec=get_existing_agents, return_value=agents),
        ),
        patch(
            f"{_MODULE}._collect_provider_models",
            AsyncMock(spec=collect_provider_models, return_value=provider_models),
        ),
    ):
        state = State()
        state.app_state = app_state
        # Annotated because ``handler.fn`` is typed as a bare callable, so the
        # awaited result would otherwise be ``Any`` and every assertion below
        # would type-check vacuously.
        response: ApiResponse[
            SetupModelRecommendationsResponse
        ] = await SetupCompanyController.get_model_recommendations.fn(
            _controller(), state=state
        )
    data = response.data
    assert data is not None
    return data


class TestRecommendationsMatchCandidates:
    async def test_every_recommendation_is_one_of_the_offered_refs(self) -> None:
        agents = [_agent("live-provider", "model-a")]

        data = await _recommendations(agents, (("live-provider", "model-a"),))

        offered = {candidate.ref for candidate in data.model_ref_candidates}
        assert offered == {_ref("live-provider", "model-a")}
        for recommended in (
            data.decomposition_recommended,
            data.research_recommended,
            data.cos_recommended,
            data.propose_recommended,
            data.routing_recommended,
            data.narrative_recommended,
            data.charter_recommended,
        ):
            assert recommended in offered

    async def test_a_removed_provider_yields_no_recommendation(self) -> None:
        """The agent still names a provider the live configs no longer serve."""
        agents = [_agent("deleted-provider", "model-a")]

        data = await _recommendations(agents, (("live-provider", "model-a"),))

        # Not merely a different value: offering nothing is what leaves the
        # picker showing its placeholder rather than an invisible selection.
        assert data.decomposition_recommended is None
        assert data.research_recommended is None
        assert data.cos_recommended is None
        assert data.charter_recommended is None
        assert data.model_ref_candidates[0].ref == _ref("live-provider", "model-a")

    async def test_a_removed_model_yields_no_recommendation(self) -> None:
        """Same provider, but the model id is gone from the catalogue."""
        agents = [_agent("live-provider", "retired-model")]

        data = await _recommendations(agents, (("live-provider", "model-a"),))

        assert data.decomposition_recommended is None
        assert data.routing_recommended is None

    async def test_no_providers_offers_nothing(self) -> None:
        agents = [_agent("live-provider", "model-a")]

        data = await _recommendations(agents, ())

        assert data.model_ref_candidates == ()
        assert data.decomposition_recommended is None
