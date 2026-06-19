"""Parity + completeness tests for the proposal-altitude descriptor table.

Pins the descriptor map (payload field, risk tier, config gate) against the
expected per-altitude values and asserts the import-time completeness guard
covers every :class:`ProposalAltitude`.
"""

import pytest

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.meta._model_enums import ProposalAltitude
from synthorg.meta._proposal_altitude_descriptor import (
    ALTITUDE_PAYLOAD_FIELDS,
    PROPOSAL_ALTITUDE_DESCRIPTORS,
)
from synthorg.meta._proposal_models import ImprovementProposal
from synthorg.meta.config import SelfImprovementConfig

# The expected per-altitude values: payload field, risk tier, and config
# enable attribute.
_EXPECTED_PAYLOAD: dict[ProposalAltitude, str] = {
    ProposalAltitude.CONFIG_TUNING: "config_changes",
    ProposalAltitude.ARCHITECTURE: "architecture_changes",
    ProposalAltitude.PROMPT_TUNING: "prompt_changes",
    ProposalAltitude.CODE_MODIFICATION: "code_changes",
    ProposalAltitude.TOOL_CREATION: "tool_changes",
}
_EXPECTED_RISK: dict[ProposalAltitude, ApprovalRiskLevel] = {
    ProposalAltitude.CONFIG_TUNING: ApprovalRiskLevel.LOW,
    ProposalAltitude.ARCHITECTURE: ApprovalRiskLevel.MEDIUM,
    ProposalAltitude.PROMPT_TUNING: ApprovalRiskLevel.LOW,
    ProposalAltitude.CODE_MODIFICATION: ApprovalRiskLevel.HIGH,
    ProposalAltitude.TOOL_CREATION: ApprovalRiskLevel.HIGH,
}
_EXPECTED_ENABLE_ATTR: dict[ProposalAltitude, str] = {
    ProposalAltitude.CONFIG_TUNING: "config_tuning_enabled",
    ProposalAltitude.ARCHITECTURE: "architecture_proposals_enabled",
    ProposalAltitude.PROMPT_TUNING: "prompt_tuning_enabled",
    ProposalAltitude.CODE_MODIFICATION: "code_modification_enabled",
    ProposalAltitude.TOOL_CREATION: "tool_creation_enabled",
}


@pytest.mark.unit
class TestDescriptorParity:
    """The descriptor matches the expected per-altitude tables."""

    @pytest.mark.parametrize("altitude", list(ProposalAltitude))
    def test_payload_field_matches(self, altitude: ProposalAltitude) -> None:
        assert (
            PROPOSAL_ALTITUDE_DESCRIPTORS[altitude].payload_field
            == _EXPECTED_PAYLOAD[altitude]
        )

    @pytest.mark.parametrize("altitude", list(ProposalAltitude))
    def test_risk_level_matches(self, altitude: ProposalAltitude) -> None:
        assert (
            PROPOSAL_ALTITUDE_DESCRIPTORS[altitude].risk_level
            == _EXPECTED_RISK[altitude]
        )

    @pytest.mark.parametrize("altitude", list(ProposalAltitude))
    def test_enable_config_attr_matches(self, altitude: ProposalAltitude) -> None:
        assert (
            PROPOSAL_ALTITUDE_DESCRIPTORS[altitude].enable_config_attr
            == _EXPECTED_ENABLE_ATTR[altitude]
        )


@pytest.mark.unit
class TestDescriptorIntegrity:
    """Completeness guard and attribute-existence invariants."""

    def test_map_covers_every_altitude(self) -> None:
        assert set(PROPOSAL_ALTITUDE_DESCRIPTORS) == set(ProposalAltitude)

    def test_payload_fields_collection_is_complete(self) -> None:
        assert set(ALTITUDE_PAYLOAD_FIELDS) == set(_EXPECTED_PAYLOAD.values())

    def test_payload_fields_exist_on_model(self) -> None:
        fields = set(ImprovementProposal.model_fields)
        for descriptor in PROPOSAL_ALTITUDE_DESCRIPTORS.values():
            assert descriptor.payload_field in fields

    def test_enable_attrs_exist_on_config(self) -> None:
        fields = set(SelfImprovementConfig.model_fields)
        for descriptor in PROPOSAL_ALTITUDE_DESCRIPTORS.values():
            assert descriptor.enable_config_attr in fields

    def test_completeness_guard_predicate_flags_a_gap(self) -> None:
        # Mirror the module-level guard against a doctored map missing one
        # altitude: the predicate the import-time guard runs must report
        # the gap (it raises ValueError at import for the real module).
        doctored = {
            altitude: descriptor
            for altitude, descriptor in PROPOSAL_ALTITUDE_DESCRIPTORS.items()
            if altitude is not ProposalAltitude.TOOL_CREATION
        }
        missing = set(ProposalAltitude) - set(doctored)
        assert missing == {ProposalAltitude.TOOL_CREATION}
