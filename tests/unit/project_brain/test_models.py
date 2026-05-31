"""Unit tests for :mod:`synthorg.project_brain.models`.

Asserts the structural invariants the rest of the engine relies on: frozen plus
extra-forbid, the discriminated payload union resolving on ``entry_kind``, the
envelope cross-checking kind against payload, per-kind legal-status enforcement,
unique tags, and bounded confidence.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.project_brain.models import (
    BlockerPayload,
    BlockerSeverity,
    BrainEntry,
    BrainEntryKind,
    BrainEntryStatus,
    Citation,
    CitationKind,
    DecisionPayload,
    DependencyKind,
    DependencyPayload,
    OpenQuestionPayload,
    PlanRevisionPayload,
    RiskLevel,
    RiskPayload,
    legal_statuses_for,
)

pytestmark = pytest.mark.unit


def _ts() -> datetime:
    return datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)


def _decision(**overrides: object) -> BrainEntry:
    fields: dict[str, object] = {
        "project_id": "proj-1",
        "revision": 1,
        "entry_kind": BrainEntryKind.DECISION,
        "title": "Use append-only storage",
        "rationale": "A full why/when history of decisions is required.",
        "status": BrainEntryStatus.ACCEPTED,
        "author": "agent_alice",
        "recorded_at": _ts(),
        "payload": DecisionPayload(
            decision_outcome="append-only",
            alternatives=("mutable current row",),
        ),
    }
    fields.update(overrides)
    return BrainEntry(**fields)  # type: ignore[arg-type]


class TestBrainEntryEnvelope:
    """Structural invariants for the envelope model."""

    def test_minimal_decision_constructs(self) -> None:
        entry = _decision()
        assert entry.entry_kind is BrainEntryKind.DECISION
        assert entry.revision == 1
        assert entry.entry_id  # default-factory assigned a uuid

    def test_model_is_frozen(self) -> None:
        entry = _decision()
        with pytest.raises(ValidationError):
            entry.title = "changed"  # type: ignore[misc]

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            _decision(unexpected="x")

    def test_revision_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            _decision(revision=0)

    def test_confidence_bounds_enforced(self) -> None:
        assert _decision(confidence=0.5).confidence == 0.5
        with pytest.raises(ValidationError):
            _decision(confidence=1.5)

    def test_duplicate_tags_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _decision(tags=("a", "a"))


class TestKindPayloadAgreement:
    """The envelope kind must agree with the discriminated payload kind."""

    def test_kind_mismatch_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _decision(
                entry_kind=BrainEntryKind.RISK,
                status=BrainEntryStatus.ACTIVE,
            )

    def test_payload_discriminator_resolves(self) -> None:
        entry = BrainEntry(
            project_id="proj-1",
            revision=1,
            entry_kind=BrainEntryKind.BLOCKER,
            title="DB migration not signed off",
            rationale="Cannot deploy until the DBA approves.",
            status=BrainEntryStatus.BLOCKED,
            author="agent_bob",
            recorded_at=_ts(),
            payload=BlockerPayload(severity=BlockerSeverity.HIGH),
        )
        assert isinstance(entry.payload, BlockerPayload)
        assert entry.payload.severity is BlockerSeverity.HIGH


class TestLegalStatusPerKind:
    """Each kind only accepts its legal status subset."""

    def test_decision_rejects_resolved(self) -> None:
        with pytest.raises(ValidationError):
            _decision(status=BrainEntryStatus.RESOLVED)

    @pytest.mark.parametrize(
        "status", [BrainEntryStatus.OPEN, BrainEntryStatus.RESOLVED]
    )
    def test_open_question_accepts_status(self, status: BrainEntryStatus) -> None:
        entry = BrainEntry(
            project_id="proj-1",
            revision=1,
            entry_kind=BrainEntryKind.OPEN_QUESTION,
            title="Which queue backend?",
            rationale="Throughput target is unclear.",
            status=status,
            author="agent_alice",
            recorded_at=_ts(),
            payload=OpenQuestionPayload(),
        )
        assert entry.status is status

    def test_risk_rejects_cleared(self) -> None:
        with pytest.raises(ValidationError):
            BrainEntry(
                project_id="proj-1",
                revision=1,
                entry_kind=BrainEntryKind.RISK,
                title="Vendor rate limit",
                rationale="Upstream may throttle us at scale.",
                status=BrainEntryStatus.CLEARED,
                author="agent_alice",
                recorded_at=_ts(),
                payload=RiskPayload(
                    likelihood=RiskLevel.MEDIUM,
                    impact=RiskLevel.HIGH,
                ),
            )

    def test_legal_statuses_for_is_total(self) -> None:
        for kind in BrainEntryKind:
            assert legal_statuses_for(kind)


class TestPayloads:
    """Per-kind payload field constraints."""

    def test_dependency_payload_round_trips(self) -> None:
        payload = DependencyPayload(
            depends_on="task-42",
            dependency_kind=DependencyKind.TASK,
        )
        assert payload.entry_kind is BrainEntryKind.DEPENDENCY
        assert payload.depends_on == "task-42"

    def test_plan_revision_links_predecessor(self) -> None:
        payload = PlanRevisionPayload(
            summary="Split persistence into its own wave.",
            supersedes_plan_entry_id="plan-1",
        )
        assert payload.supersedes_plan_entry_id == "plan-1"

    def test_citation_constructs(self) -> None:
        citation = Citation(
            source_ref="task-7",
            source_kind=CitationKind.TASK,
            locator="line 12",
        )
        assert citation.source_kind is CitationKind.TASK
