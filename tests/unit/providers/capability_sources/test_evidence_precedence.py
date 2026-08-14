"""The capability precedence chain: override, then evidence, then heuristic."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.core.types import NotBlankStr
from synthorg.providers.capability_assignment.models import CapabilityOverrideMap
from synthorg.providers.capability_assignment.service import (
    CapabilityAssignmentService,
)
from synthorg.providers.capability_sources.grading import CapabilityThresholds
from synthorg.providers.capability_sources.models import CapabilityScore
from synthorg.providers.enums import AuthType
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
_MEASURED = _NOW - timedelta(days=30)
_THRESHOLDS = CapabilityThresholds(
    expert_percentile=0.75,
    capable_percentile=0.35,
    max_age_days=730,
)


class _MemoryOverrideStore:
    """In-memory stand-in for the persisted override envelope."""

    def __init__(self, initial: CapabilityOverrideMap | None = None) -> None:
        self._map = initial or CapabilityOverrideMap()

    async def load(self) -> CapabilityOverrideMap:
        return self._map

    async def save(self, overrides: CapabilityOverrideMap) -> None:
        self._map = overrides


class _MemoryScores:
    """In-memory stand-in for the persisted score rows."""

    def __init__(self, rows: Sequence[CapabilityScore]) -> None:
        self._rows = tuple(rows)

    async def all_scores(self) -> Sequence[CapabilityScore]:
        return self._rows


def _score(model: str, score: float, *, source: str = "source-a") -> CapabilityScore:
    return CapabilityScore(
        source_label=NotBlankStr(source),
        model_identifier=NotBlankStr(model),
        axis="general",
        score=score,
        as_of=_MEASURED,
        ingested_at=_NOW,
    )


def _cohort(source: str = "source-a") -> list[CapabilityScore]:
    """Ten filler models so the graded subject has something to rank against."""
    return [_score(f"filler-{i:02d}", i * 5.0, source=source) for i in range(10)]


def _model(model_id: str) -> ProviderModelConfig:
    """A model the heuristic grades ``basic`` with high confidence.

    Pinning the heuristic low means an evidence-led ``expert`` cannot be
    mistaken for the heuristic having agreed by coincidence.
    """
    return ProviderModelConfig(
        id=model_id,
        metadata=ModelMetadata(cost_tier=1),
    )


def _provider(*model_ids: str) -> ProviderConfig:
    return ProviderConfig(
        auth_type=AuthType.NONE,
        models=tuple(_model(model_id) for model_id in model_ids),
    )


def _providers(*model_ids: str) -> dict[str, ProviderConfig]:
    return {"provider-a": _provider(*model_ids)}


def _service(
    *,
    scores: Sequence[CapabilityScore] | None = None,
    overrides: CapabilityOverrideMap | None = None,
) -> CapabilityAssignmentService:
    return CapabilityAssignmentService(
        store=_MemoryOverrideStore(overrides),
        scores=_MemoryScores(scores) if scores is not None else None,
        thresholds=_THRESHOLDS if scores is not None else None,
        clock=FakeClock(start=_NOW),
    )


class TestPrecedence:
    async def test_evidence_beats_the_heuristic(self) -> None:
        service = _service(scores=[*_cohort(), _score("model-y", 100.0)])
        assignments = await service.effective_assignments(_providers("model-y"))

        assert len(assignments) == 1
        assert assignments[0].capability == "expert"
        assert assignments[0].provenance == "evidence"

    async def test_the_heuristic_holds_a_model_no_source_measured(self) -> None:
        service = _service(scores=[*_cohort(), _score("model-y", 100.0)])
        assignments = await service.effective_assignments(_providers("model-z"))

        assert assignments[0].capability == "basic"
        assert assignments[0].provenance == "heuristic"

    async def test_an_operator_override_beats_evidence(self) -> None:
        service = _service(scores=[*_cohort(), _score("model-y", 100.0)])
        await service.set_override(
            provider="provider-a",
            model_id="model-y",
            capability="basic",
            provenance="operator",
            reason="measured well but it is not allowed near this work",
        )
        assignments = await service.effective_assignments(_providers("model-y"))

        assert assignments[0].capability == "basic"
        assert assignments[0].provenance == "operator"
        assert assignments[0].confidence == pytest.approx(1.0)

    async def test_no_reader_wired_leaves_the_chain_as_it_was(self) -> None:
        """An installation with no enabled source is not a degraded one."""
        service = _service()
        assignments = await service.effective_assignments(_providers("model-y"))

        assert assignments[0].provenance == "heuristic"

    async def test_an_empty_score_table_falls_through_to_the_heuristic(self) -> None:
        service = _service(scores=[])
        assignments = await service.effective_assignments(_providers("model-y"))

        assert assignments[0].provenance == "heuristic"


class TestProvenanceIsAnswerable:
    async def test_the_reason_names_the_source_standing_and_cohort(self) -> None:
        service = _service(scores=[*_cohort(), _score("model-y", 100.0)])
        assignments = await service.effective_assignments(_providers("model-y"))

        reason = str(assignments[0].reason)
        assert "source-a" in reason
        assert "11 models" in reason
        assert "general" in reason
        assert _MEASURED.date().isoformat() in reason

    async def test_evidence_is_not_claimed_as_authoritative(self) -> None:
        """Only a human decision is; a measurement is still a measurement."""
        service = _service(scores=[*_cohort(), _score("model-y", 100.0)])
        assignments = await service.effective_assignments(_providers("model-y"))

        assert assignments[0].confidence < 1.0


class TestDisagreementAcrossSources:
    async def test_the_lower_rung_wins(self) -> None:
        rows = [
            *_cohort("generous"),
            _score("model-y", 100.0, source="generous"),
            *_cohort("harsh"),
            _score("model-y", 0.0, source="harsh"),
        ]
        service = _service(scores=rows)
        assignments = await service.effective_assignments(_providers("model-y"))

        assert assignments[0].capability == "basic"
        assert "harsh" in str(assignments[0].reason)

    async def test_one_source_alone_still_grades(self) -> None:
        """A source being down withdraws its opinion, not the other's."""
        rows = [*_cohort("still-up"), _score("model-y", 100.0, source="still-up")]
        service = _service(scores=rows)
        assignments = await service.effective_assignments(_providers("model-y"))

        assert assignments[0].capability == "expert"
        assert assignments[0].provenance == "evidence"


class TestMatching:
    async def test_one_measurement_grades_the_model_on_every_provider(self) -> None:
        """Capability is a property of the model, not of the connection."""
        providers = {
            "provider-a": _provider("model-y"),
            "provider-b": _provider("model-y"),
        }
        service = _service(scores=[*_cohort(), _score("model-y", 100.0)])
        assignments = await service.effective_assignments(providers)

        assert [a.provenance for a in assignments] == ["evidence", "evidence"]
        assert {str(a.provider) for a in assignments} == {"provider-a", "provider-b"}

    async def test_a_variant_identifier_does_not_grade_the_base_model(self) -> None:
        service = _service(scores=[*_cohort(), _score("model-y_high", 100.0)])
        assignments = await service.effective_assignments(_providers("model-y"))

        assert assignments[0].provenance == "heuristic"


class TestLookup:
    async def test_the_resolver_map_carries_the_evidence_rung(self) -> None:
        service = _service(scores=[*_cohort(), _score("model-y", 100.0)])
        lookup = await service.capability_lookup(_providers("model-y"))

        assert lookup == {("provider-a", "model-y"): "expert"}
