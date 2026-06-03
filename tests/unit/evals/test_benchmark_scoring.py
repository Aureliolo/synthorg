"""Unit tests for per-model benchmark scoring + seed serialisation."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.benchmark_scoring import (
    BENCHMARK_SCORE_SOURCE,
    load_manifest,
    score_model_from_cassette,
    score_record_from_scorecard,
    serialise_seed_records,
)
from evals.errors import CassetteNotFoundError
from evals.models.brief import BriefKind
from evals.models.scorecard import (
    AggregatedProcessFacts,
    BriefResult,
    ProcessFactReport,
    Scorecard,
)
from synthorg.budget.benchmark_seed import load_seed_records
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


def _brief_result(*, brief_id: str, grade: int) -> BriefResult:
    return BriefResult(
        brief_id=brief_id,
        kind=BriefKind.EXECUTABLE,
        grade=grade,
        deduction=0,
        score=grade,
        process_facts=ProcessFactReport(events_by_class={}, entries=()),
        termination_reason=NotBlankStr("COMPLETED"),
        judge_calibration=None,
    )


def _scorecard(grades: tuple[int, ...]) -> Scorecard:
    briefs = tuple(
        _brief_result(brief_id=f"BRIEF_{i:03d}", grade=g) for i, g in enumerate(grades)
    )
    return Scorecard(
        generated_at=_NOW,
        company_config_path=NotBlankStr("evals/benchmark_scores/single_agent.yaml"),
        cassette_path=NotBlankStr("cassette:example-large-001.json"),
        cassette_sha256=NotBlankStr("a" * 64),
        suite_version=NotBlankStr("sha256:suite01"),
        briefs=briefs,
        process_facts=AggregatedProcessFacts(total_events=0, events_by_class={}),
    )


class TestScoreRecordFromScorecard:
    def test_mean_and_provenance(self) -> None:
        record = score_record_from_scorecard(
            _scorecard((90, 80, 100)),
            model_id=NotBlankStr("example-large-001"),
            generated_at=_NOW,
        )
        assert record.model_id == "example-large-001"
        assert record.score == pytest.approx(90.0)
        assert record.source == BENCHMARK_SCORE_SOURCE
        assert record.suite_version == "sha256:suite01"
        assert record.cassette_sha256 == "a" * 64
        # Band brackets the mean and stays within [0, 100].
        assert record.confidence_lower <= record.score <= record.confidence_upper
        assert record.confidence_lower >= 0.0
        assert record.confidence_upper <= 100.0
        # Non-degenerate spread yields a real interval.
        assert record.confidence_lower < record.confidence_upper

    def test_single_brief_is_point_estimate(self) -> None:
        record = score_record_from_scorecard(
            _scorecard((73,)),
            model_id=NotBlankStr("example-small-001"),
            generated_at=_NOW,
        )
        assert record.score == pytest.approx(73.0)
        # One brief -> zero standard error -> degenerate band at the mean.
        assert record.confidence_lower == pytest.approx(73.0)
        assert record.confidence_upper == pytest.approx(73.0)


class TestSeedSerialisation:
    def test_round_trips_through_load_seed_records(self, tmp_path: Path) -> None:
        records = (
            score_record_from_scorecard(
                _scorecard((90, 80, 100)),
                model_id=NotBlankStr("example-large-001"),
                generated_at=_NOW,
            ),
            score_record_from_scorecard(
                _scorecard((73,)),
                model_id=NotBlankStr("example-small-001"),
                generated_at=_NOW,
            ),
        )
        seed_path = tmp_path / "benchmark_seed.json"
        seed_path.write_text(serialise_seed_records(records), encoding="utf-8")

        loaded = load_seed_records(seed_path)
        assert {r.model_id for r in loaded} == {
            "example-large-001",
            "example-small-001",
        }
        # Ordered by model_id (large before small).
        assert [r.model_id for r in loaded] == [
            "example-large-001",
            "example-small-001",
        ]


class TestManifest:
    def test_committed_manifest_parses(self) -> None:
        manifest = load_manifest(Path("evals/benchmark_scores/models.yaml"))
        assert manifest.brief_suite == "evals/briefs"
        assert len(manifest.models) >= 1
        ids = {m.model_id for m in manifest.models}
        assert "example-large-001" in ids


class TestMissingCassetteRefusal:
    async def test_replay_refuses_missing_cassette(self, tmp_path: Path) -> None:
        with pytest.raises(CassetteNotFoundError):
            await score_model_from_cassette(
                model_id=NotBlankStr("example-large-001"),
                company_config=Path("evals/benchmark_scores/single_agent.yaml"),
                brief_suite=Path("evals/briefs"),
                cassette=tmp_path / "does-not-exist.json",
                out_dir=tmp_path / "out",
                provider_name="example-provider",
                generated_at=_NOW,
            )
