"""Unit tests for toolsmith domain models."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from synthorg.meta.toolsmith.models import (
    CapabilityGap,
    ToolBlueprint,
    ToolBlueprintState,
    ToolSandboxBackend,
    ToolValidationResult,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


def _blueprint(**overrides: object) -> ToolBlueprint:
    base: dict[str, object] = {
        "id": "bp-1",
        "name": "synthorg_textkit_slugify",
        "description": "Slugify text deterministically.",
        "capability": "textkit:slugify",
        "parameters_schema": _SCHEMA,
        "script_body": "print('ok')",
        "action_type": "code:read",
        "created_at": _NOW,
    }
    base.update(overrides)
    return ToolBlueprint(**base)  # type: ignore[arg-type]


class TestCapabilityGap:
    def test_valid(self) -> None:
        gap = CapabilityGap(
            signature="textkit:slugify",
            occurrences=3,
            first_seen=_NOW,
            last_seen=_NOW + timedelta(hours=1),
        )
        assert gap.occurrences == 3

    def test_occurrences_floor(self) -> None:
        with pytest.raises(ValidationError):
            CapabilityGap(
                signature="x:y",
                occurrences=0,
                first_seen=_NOW,
                last_seen=_NOW,
            )

    def test_ordering_enforced(self) -> None:
        with pytest.raises(ValidationError):
            CapabilityGap(
                signature="x:y",
                occurrences=1,
                first_seen=_NOW,
                last_seen=_NOW - timedelta(hours=1),
            )


class TestToolValidationResult:
    def test_pass_requires_brief_and_nonnegative_margin(self) -> None:
        result = ToolValidationResult(
            passed=True,
            brief_passed=True,
            brief_score=90,
            baseline_score=100,
            candidate_score=100,
            margin=0,
            detail="ok",
        )
        assert result.passed

    def test_margin_arithmetic_enforced(self) -> None:
        with pytest.raises(ValidationError):
            ToolValidationResult(
                passed=True,
                brief_passed=True,
                brief_score=90,
                baseline_score=100,
                candidate_score=105,
                margin=1,
                detail="bad margin",
            )

    def test_pass_predicate_enforced(self) -> None:
        with pytest.raises(ValidationError):
            ToolValidationResult(
                passed=True,
                brief_passed=False,
                brief_score=10,
                baseline_score=100,
                candidate_score=100,
                margin=0,
                detail="brief failed",
            )

    def test_regression_blocks_pass(self) -> None:
        with pytest.raises(ValidationError):
            ToolValidationResult(
                passed=True,
                brief_passed=True,
                brief_score=90,
                baseline_score=100,
                candidate_score=95,
                margin=-5,
                detail="regressed",
            )


class TestToolBlueprint:
    def test_defaults(self) -> None:
        bp = _blueprint()
        assert bp.state is ToolBlueprintState.PENDING
        assert bp.sandbox_backend is ToolSandboxBackend.DOCKER
        assert bp.requires_network is False
        assert bp.validation is None

    def test_frozen(self) -> None:
        bp = _blueprint()
        with pytest.raises(ValidationError):
            bp.name = "synthorg_other_thing"  # type: ignore[misc]

    @pytest.mark.parametrize(
        "bad_name", ["slugify", "synthorg_slugify", "Synthorg_a_b"]
    )
    def test_name_contract(self, bad_name: str) -> None:
        with pytest.raises(ValidationError):
            _blueprint(name=bad_name)

    @pytest.mark.parametrize("bad_cap", ["slugify", "textkit/slugify", "Text:Slug"])
    def test_capability_contract(self, bad_cap: str) -> None:
        with pytest.raises(ValidationError):
            _blueprint(capability=bad_cap)

    def test_action_type_contract(self) -> None:
        with pytest.raises(ValidationError):
            _blueprint(action_type="invalid")

    def test_schema_must_be_object_with_properties(self) -> None:
        with pytest.raises(ValidationError):
            _blueprint(parameters_schema={"type": "string"})
        with pytest.raises(ValidationError):
            _blueprint(parameters_schema={"type": "object"})

    def test_validated_state_requires_timestamp(self) -> None:
        with pytest.raises(ValidationError):
            _blueprint(state=ToolBlueprintState.VALIDATED)

    def test_active_state_requires_timestamps(self) -> None:
        with pytest.raises(ValidationError):
            _blueprint(
                state=ToolBlueprintState.ACTIVE,
                validated_at=_NOW,
            )

    def test_retired_state_requires_timestamp(self) -> None:
        with pytest.raises(ValidationError):
            _blueprint(state=ToolBlueprintState.RETIRED)

    def test_active_blueprint_round_trip(self) -> None:
        bp = _blueprint(
            state=ToolBlueprintState.ACTIVE,
            validated_at=_NOW + timedelta(minutes=1),
            activated_at=_NOW + timedelta(minutes=2),
            validation=ToolValidationResult(
                passed=True,
                brief_passed=True,
                brief_score=88,
                baseline_score=100,
                candidate_score=101,
                margin=1,
                detail="passed",
            ),
        )
        assert bp.state is ToolBlueprintState.ACTIVE
        assert bp.validation is not None
        assert bp.validation.passed

    def test_timestamp_ordering_enforced(self) -> None:
        with pytest.raises(ValidationError):
            _blueprint(
                state=ToolBlueprintState.ACTIVE,
                validated_at=_NOW + timedelta(minutes=5),
                activated_at=_NOW + timedelta(minutes=2),
            )

    def test_active_requires_validation_record(self) -> None:
        # An ACTIVE tool is live-registered, which the applier only does
        # after the benchmark gate passes and persists the result; a row
        # in ACTIVE without a validation record indicates a corrupt write.
        with pytest.raises(ValidationError):
            _blueprint(
                state=ToolBlueprintState.ACTIVE,
                validated_at=_NOW + timedelta(minutes=1),
                activated_at=_NOW + timedelta(minutes=2),
                validation=None,
            )

    def test_validated_requires_validation_record(self) -> None:
        # Same invariant as ACTIVE: a VALIDATED row without the gate
        # result has lost the audit evidence the state name promises.
        with pytest.raises(ValidationError):
            _blueprint(
                state=ToolBlueprintState.VALIDATED,
                validated_at=_NOW + timedelta(minutes=1),
                validation=None,
            )

    def test_retired_requires_validation_record(self) -> None:
        # A RETIRED tool was once ACTIVE; the gate evidence MUST survive
        # the lifecycle so post-retirement audits can replay the apply.
        with pytest.raises(ValidationError):
            _blueprint(
                state=ToolBlueprintState.RETIRED,
                validated_at=_NOW + timedelta(minutes=1),
                activated_at=_NOW + timedelta(minutes=2),
                retired_at=_NOW + timedelta(minutes=3),
                validation=None,
            )

    def test_name_must_match_capability_domain_action(self) -> None:
        # ``synthorg_textkit_slugify`` declares domain=textkit,
        # action=slugify; if the capability disagrees, routing and
        # governance see two different identifiers for the same tool.
        with pytest.raises(ValidationError):
            _blueprint(
                name="synthorg_textkit_slugify",
                capability="other:slugify",
            )
        with pytest.raises(ValidationError):
            _blueprint(
                name="synthorg_textkit_slugify",
                capability="textkit:other",
            )
