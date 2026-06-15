"""Per-altitude descriptor table for proposal dispatch.

Centralises the three per-altitude facts that the proposal model, the
signals service, and the scope-check guard each need: the payload field
an altitude's proposal must carry, the approval risk tier it routes to,
and the self-improvement config flag that gates it. A single
``MappingProxyType`` keyed by every altitude, plus an import-time
completeness guard, means adding a new altitude fails loudly at import
unless its descriptor is supplied -- instead of silently producing a
wrong result at one of the call sites.
"""

from dataclasses import dataclass
from types import MappingProxyType

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.meta._model_enums import ProposalAltitude


@dataclass(frozen=True)
class ProposalAltitudeDescriptor:
    """Dispatch metadata for one :class:`ProposalAltitude`.

    Attributes:
        payload_field: Name of the ``ImprovementProposal`` field that an
            altitude's proposal must populate (and the only one it may).
        risk_level: Approval risk tier the altitude routes to.
        enable_config_attr: Name of the ``SelfImprovementConfig`` boolean
            that gates whether the altitude is in scope.
    """

    payload_field: str
    risk_level: ApprovalRiskLevel
    enable_config_attr: str


PROPOSAL_ALTITUDE_DESCRIPTORS: MappingProxyType[
    ProposalAltitude, ProposalAltitudeDescriptor
] = MappingProxyType(
    {
        ProposalAltitude.CONFIG_TUNING: ProposalAltitudeDescriptor(
            payload_field="config_changes",
            risk_level=ApprovalRiskLevel.LOW,
            enable_config_attr="config_tuning_enabled",
        ),
        ProposalAltitude.ARCHITECTURE: ProposalAltitudeDescriptor(
            payload_field="architecture_changes",
            risk_level=ApprovalRiskLevel.MEDIUM,
            enable_config_attr="architecture_proposals_enabled",
        ),
        ProposalAltitude.PROMPT_TUNING: ProposalAltitudeDescriptor(
            payload_field="prompt_changes",
            risk_level=ApprovalRiskLevel.LOW,
            enable_config_attr="prompt_tuning_enabled",
        ),
        ProposalAltitude.CODE_MODIFICATION: ProposalAltitudeDescriptor(
            payload_field="code_changes",
            risk_level=ApprovalRiskLevel.HIGH,
            enable_config_attr="code_modification_enabled",
        ),
        ProposalAltitude.TOOL_CREATION: ProposalAltitudeDescriptor(
            payload_field="tool_changes",
            risk_level=ApprovalRiskLevel.HIGH,
            enable_config_attr="tool_creation_enabled",
        ),
    }
)

# All payload fields across altitudes, used by the changes/altitude
# consistency validator to assert only the declared field is populated.
ALTITUDE_PAYLOAD_FIELDS: tuple[str, ...] = tuple(
    descriptor.payload_field for descriptor in PROPOSAL_ALTITUDE_DESCRIPTORS.values()
)


_missing_altitudes = set(ProposalAltitude) - set(PROPOSAL_ALTITUDE_DESCRIPTORS)
if _missing_altitudes:
    _msg = (
        f"Missing PROPOSAL_ALTITUDE_DESCRIPTORS entries for: "
        f"{sorted(a.value for a in _missing_altitudes)}"
    )
    raise ValueError(_msg)

del _missing_altitudes
