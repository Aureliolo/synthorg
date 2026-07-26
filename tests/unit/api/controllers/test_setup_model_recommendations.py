"""Tests for the setup wizard's model-recommendation DTOs.

The contract pinned here is the one the wizard's model pickers depend on: every
per-feature model setting is a ``MODEL_REF`` that rejects a provider-less value
at write time, so a candidate's ``ref`` must be a value the settings validator
accepts. The round-trip assertion below is the decisive one; without it the
endpoint can emit a shape the write path refuses and nothing fails until an
operator clicks the picker.
"""

import pytest
from pydantic import ValidationError

from synthorg.api.controllers.setup_model_recommendations import (
    SetupModelCandidate,
    SetupModelRecommendationsResponse,
)
from synthorg.settings import (
    definitions as _settings_definitions,  # noqa: F401 -- side-effect import populates the registry
)
from synthorg.settings.enums import SettingType
from synthorg.settings.errors import SettingValidationError
from synthorg.settings.model_ref import parse_model_ref
from synthorg.settings.registry import get_registry
from synthorg.settings.type_validators import validate_by_type

# Every per-feature model picker the wizard renders, paired with the setting it
# writes to. The embedding picker is deliberately absent: ``memory.embedder_model``
# is a plain STRING setting and keeps a bare model id.
_MODEL_REF_SETTINGS: tuple[tuple[str, str], ...] = (
    ("coordination", "decomposition_model"),
    ("research", "model"),
    ("chief_of_staff", "chat_model"),
    ("chief_of_staff", "propose_model"),
    ("chief_of_staff", "routing_model"),
    ("chief_of_staff", "narrative_model"),
    ("charter", "interview_model"),
)


def _candidate() -> SetupModelCandidate:
    """A representative provider-bound candidate."""
    return SetupModelCandidate(provider="test-provider", model_id="test-model-001")


@pytest.mark.unit
class TestSetupModelCandidate:
    def test_ref_is_derived_from_its_own_provider_and_model(self) -> None:
        parsed = parse_model_ref(_candidate().ref)
        assert parsed.provider == "test-provider"
        assert parsed.model_id == "test-model-001"
        assert parsed.is_bound

    def test_ref_cannot_be_supplied_out_of_band(self) -> None:
        # ``ref`` is computed, so no constructor argument can desync it from
        # the provider and model it claims to reference.
        with pytest.raises(ValidationError):
            SetupModelCandidate(
                provider="test-provider",
                model_id="test-model-001",
                ref='{"provider": "other", "model_id": "other"}',  # type: ignore[call-arg]
            )

    def test_ref_serialises_into_the_response_payload(self) -> None:
        candidate = _candidate()
        # The dashboard reads ``ref`` off the wire, so a computed field that
        # did not serialise would silently strand every picker.
        assert candidate.model_dump()["ref"] == candidate.ref

    def test_same_model_id_on_two_providers_yields_distinct_refs(self) -> None:
        first = SetupModelCandidate(provider="provider-a", model_id="shared-001")
        second = SetupModelCandidate(provider="provider-b", model_id="shared-001")
        assert first.ref != second.ref


@pytest.mark.unit
@pytest.mark.parametrize(("namespace", "key"), _MODEL_REF_SETTINGS)
class TestCandidateRefIsWritable:
    """A candidate ref must survive the validator guarding each picker."""

    def test_setting_is_a_model_ref(self, namespace: str, key: str) -> None:
        definition = get_registry().get(namespace, key)
        assert definition is not None, f"{namespace}/{key} is not registered"
        assert definition.type == SettingType.MODEL_REF

    def test_candidate_ref_passes_write_validation(
        self,
        namespace: str,
        key: str,
    ) -> None:
        definition = get_registry().get(namespace, key)
        assert definition is not None
        # Raises SettingValidationError if the wizard could not persist it.
        validate_by_type(definition, _candidate().ref)

    def test_a_bare_model_id_is_still_rejected(
        self,
        namespace: str,
        key: str,
    ) -> None:
        definition = get_registry().get(namespace, key)
        assert definition is not None
        with pytest.raises(SettingValidationError):
            validate_by_type(definition, "test-model-001")


@pytest.mark.unit
class TestSetupModelRecommendationsResponse:
    def test_embedding_recommendation_requires_its_dims(self) -> None:
        with pytest.raises(ValidationError):
            SetupModelRecommendationsResponse(embedding_recommended="embed-001")

    def test_embedding_dims_require_a_recommendation(self) -> None:
        with pytest.raises(ValidationError):
            SetupModelRecommendationsResponse(embedding_recommended_dims=1024)

    def test_candidates_carry_refs_the_recommendations_can_match(self) -> None:
        candidate = _candidate()
        response = SetupModelRecommendationsResponse(
            model_ref_candidates=(candidate,),
            decomposition_recommended=candidate.ref,
        )
        # The picker preselects by matching the recommendation against an
        # option value, so the two must be the same string.
        first = response.model_ref_candidates[0]
        assert response.decomposition_recommended == first.ref
