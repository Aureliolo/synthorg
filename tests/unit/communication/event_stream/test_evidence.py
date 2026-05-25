# mypy: disable-error-code="explicit-any"
"""Tests for EvidencePackage and RecommendedAction models."""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from synthorg.core.enums import ApprovalRiskLevel
from synthorg.core.evidence import (
    EvidencePackage,
    EvidencePackageSignature,
    RecommendedAction,
)
from synthorg.core.structured_artifact import StructuredArtifact


def _make_action(**overrides: Any) -> RecommendedAction:
    defaults: dict[str, Any] = {
        "action_type": "approve",
        "label": "Approve",
        "description": "Approve this action",
    }
    defaults.update(overrides)
    return RecommendedAction(**defaults)


def _make_evidence(**overrides: Any) -> EvidencePackage:
    defaults: dict[str, Any] = {
        "id": "ep-001",
        "title": "Tool execution approval",
        "narrative": "Agent requests permission to execute deploy tool.",
        "reasoning_trace": ("Step 1: analyzed risk", "Step 2: classified"),
        "recommended_actions": (_make_action(),),
        "source_agent_id": "agent-eng-001",
        "task_id": "task-123",
        "created_at": datetime(2026, 4, 13, tzinfo=UTC),
        "risk_level": ApprovalRiskLevel.MEDIUM,
    }
    defaults.update(overrides)
    return EvidencePackage(**defaults)


@pytest.mark.unit
class TestRecommendedAction:
    def test_construction(self) -> None:
        action = _make_action()
        assert action.action_type == "approve"
        assert action.label == "Approve"
        assert action.confirmation_required is False

    def test_confirmation_required(self) -> None:
        action = _make_action(confirmation_required=True)
        assert action.confirmation_required is True

    def test_frozen(self) -> None:
        action = _make_action()
        with pytest.raises(ValidationError, match="frozen"):
            action.label = "Changed"  # type: ignore[misc]

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"action_type": ""}, "at least 1"),
            ({"label": ""}, "at least 1"),
            ({"description": ""}, "at least 1"),
            ({"label": "   "}, "whitespace"),
        ],
    )
    def test_blank_fields_rejected(
        self,
        kwargs: dict[str, str],
        match: str,
    ) -> None:
        with pytest.raises(ValueError, match=match):
            _make_action(**kwargs)


@pytest.mark.unit
class TestEvidencePackage:
    def test_construction(self) -> None:
        ep = _make_evidence()
        assert ep.id == "ep-001"
        assert ep.title == "Tool execution approval"
        assert ep.risk_level == ApprovalRiskLevel.MEDIUM
        assert len(ep.recommended_actions) == 1
        assert len(ep.reasoning_trace) == 2

    def test_extends_structured_artifact(self) -> None:
        ep = _make_evidence()
        assert isinstance(ep, StructuredArtifact)

    def test_frozen(self) -> None:
        ep = _make_evidence()
        with pytest.raises(ValidationError, match="frozen"):
            ep.title = "Changed"  # type: ignore[misc]

    def test_metadata_deep_copied(self) -> None:
        original: dict[str, object] = {
            "key": "value",
            "nested": {"a": 1},
        }
        ep = _make_evidence(metadata=original)
        original["key"] = "mutated"
        original["nested"]["a"] = 2  # type: ignore[index]
        assert ep.metadata["key"] == "value"
        assert ep.metadata["nested"]["a"] == 1  # type: ignore[index]

    def test_empty_recommended_actions_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            _make_evidence(recommended_actions=())

    def test_multiple_recommended_actions(self) -> None:
        actions = (
            _make_action(action_type="approve", label="Approve"),
            _make_action(action_type="reject", label="Reject"),
            _make_action(action_type="revise", label="Revise"),
        )
        ep = _make_evidence(recommended_actions=actions)
        assert len(ep.recommended_actions) == 3

    def test_optional_task_id(self) -> None:
        ep = _make_evidence(task_id=None)
        assert ep.task_id is None

    @pytest.mark.parametrize(
        "field",
        ["id", "title", "narrative", "source_agent_id"],
    )
    def test_blank_fields_rejected(self, field: str) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            _make_evidence(**{field: ""})

    def test_too_many_recommended_actions_rejected(self) -> None:
        actions = tuple(
            _make_action(action_type=f"act-{i}", label=f"Act {i}") for i in range(4)
        )
        with pytest.raises(ValueError, match="at most 3"):
            _make_evidence(recommended_actions=actions)

    def test_duplicate_signature_approver_rejected(self) -> None:
        sig = EvidencePackageSignature(
            approver_id="approver-1",
            algorithm="ed25519",
            signature_bytes=b"\x00",
            signed_at=datetime(2026, 4, 13, tzinfo=UTC),
            chain_position=0,
        )
        with pytest.raises(ValueError, match="unique approver_id"):
            _make_evidence(signatures=(sig, sig))

    def test_is_fully_signed_uses_distinct_approvers(self) -> None:
        sig_a = EvidencePackageSignature(
            approver_id="approver-a",
            algorithm="ed25519",
            signature_bytes=b"\x01",
            signed_at=datetime(2026, 4, 13, tzinfo=UTC),
            chain_position=0,
        )
        sig_b = EvidencePackageSignature(
            approver_id="approver-b",
            algorithm="ed25519",
            signature_bytes=b"\x02",
            signed_at=datetime(2026, 4, 13, tzinfo=UTC),
            chain_position=1,
        )
        ep = _make_evidence(signature_threshold=2, signatures=(sig_a, sig_b))
        assert ep.is_fully_signed is True
        ep_one = _make_evidence(signature_threshold=2, signatures=(sig_a,))
        assert ep_one.is_fully_signed is False

    def test_empty_reasoning_trace(self) -> None:
        ep = _make_evidence(reasoning_trace=())
        assert ep.reasoning_trace == ()

    def test_all_risk_levels(self) -> None:
        for level in ApprovalRiskLevel:
            ep = _make_evidence(risk_level=level)
            assert ep.risk_level == level

    def test_default_metadata_empty(self) -> None:
        ep = _make_evidence()
        assert ep.metadata == {}

    def test_created_at_from_structured_artifact(self) -> None:
        ts = datetime(2026, 1, 1, tzinfo=UTC)
        ep = _make_evidence(created_at=ts)
        assert ep.created_at == ts
