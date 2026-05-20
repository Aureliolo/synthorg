"""Tests for the scorecard model + JSON/Markdown emitters."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.emit.json_writer import write_scorecard_json
from evals.emit.markdown_writer import render_scorecard_md, write_scorecard_md
from evals.models.brief import BriefKind
from evals.models.scorecard import (
    SCORECARD_SCHEMA_VERSION,
    AggregatedProcessFacts,
    BriefResult,
    JudgeCalibrationReport,
    ProcessFactReport,
    Scorecard,
)
from evals.scoring.aggregate import PenaltyEntry


def _brief_result(  # noqa: PLR0913 -- test fixture; keeping the kw-only knobs explicit
    *,
    brief_id: str = "BRIEF_001",
    grade: int = 90,
    deduction: int = 0,
    events: dict[str, int] | None = None,
    entries: tuple[PenaltyEntry, ...] = (),
    kind: BriefKind = BriefKind.EXECUTABLE,
) -> BriefResult:
    return BriefResult(
        brief_id=brief_id,
        kind=kind,
        grade=grade,
        deduction=deduction,
        score=max(0, grade - deduction),
        process_facts=ProcessFactReport(
            events_by_class=events or {},
            entries=entries,
        ),
        termination_reason="COMPLETED",
    )


def _scorecard(briefs: tuple[BriefResult, ...]) -> Scorecard:
    aggregated_events: dict[str, int] = {}
    total = 0
    for b in briefs:
        for k, v in b.process_facts.events_by_class.items():
            aggregated_events[k] = aggregated_events.get(k, 0) + v
            total += v
    return Scorecard(
        generated_at=datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC),
        company_config_path="evals/baselines/reference.yaml",
        cassette_path="evals/cassettes/reference_run.cassette.json",
        cassette_sha256="0" * 64,
        suite_version="suite-abc123",
        briefs=briefs,
        process_facts=AggregatedProcessFacts(
            total_events=total,
            events_by_class=aggregated_events,
        ),
    )


@pytest.mark.unit
def test_clean_scorecard_round_trips_through_json(tmp_path: Path) -> None:
    sc = _scorecard((_brief_result(grade=100, deduction=0),))
    path = write_scorecard_json(sc, tmp_path)
    assert path.is_file()
    reparsed = Scorecard.model_validate_json(path.read_text(encoding="utf-8"))
    assert reparsed == sc
    assert reparsed.total == 100
    assert reparsed.is_passing is True


@pytest.mark.unit
def test_aggregated_process_facts_mismatch_rejected() -> None:
    # Brief says 2 hard_stops; aggregate claims 1 -- model_validator rejects.
    briefs = (_brief_result(events={"x.budget": 2}),)
    with pytest.raises(ValueError, match="disagrees"):
        Scorecard(
            generated_at=datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC),
            company_config_path="r.yaml",
            cassette_path="c.json",
            cassette_sha256="0" * 64,
            suite_version="s",
            briefs=briefs,
            process_facts=AggregatedProcessFacts(
                total_events=1,  # wrong
                events_by_class={"x.budget": 1},
            ),
        )


@pytest.mark.unit
def test_naive_generated_at_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Scorecard(
            generated_at=datetime(2026, 5, 20, 12, 0, 0),  # naive  # noqa: DTZ001
            company_config_path="r.yaml",
            cassette_path="c.json",
            cassette_sha256="0" * 64,
            suite_version="s",
            briefs=(_brief_result(),),
            process_facts=AggregatedProcessFacts(),
        )


@pytest.mark.unit
def test_wrong_schema_version_rejected() -> None:
    with pytest.raises(ValueError, match="schema version mismatch"):
        Scorecard(
            schema_version=SCORECARD_SCHEMA_VERSION + 1,
            generated_at=datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC),
            company_config_path="r.yaml",
            cassette_path="c.json",
            cassette_sha256="0" * 64,
            suite_version="s",
            briefs=(_brief_result(),),
            process_facts=AggregatedProcessFacts(),
        )


@pytest.mark.unit
def test_markdown_rendering_is_deterministic() -> None:
    sc = _scorecard((_brief_result(brief_id="BRIEF_001", grade=100, deduction=0),))
    rendered_a = render_scorecard_md(sc)
    rendered_b = render_scorecard_md(sc)
    assert rendered_a == rendered_b
    assert "BRIEF_001" in rendered_a
    assert "scorecard" in rendered_a.lower()
    assert "PASS" in rendered_a


@pytest.mark.unit
def test_markdown_writes_to_disk(tmp_path: Path) -> None:
    sc = _scorecard((_brief_result(),))
    path = write_scorecard_md(sc, tmp_path)
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert text == render_scorecard_md(sc)


@pytest.mark.unit
def test_markdown_includes_every_brief_id() -> None:
    sc = _scorecard(
        (
            _brief_result(brief_id="BRIEF_001"),
            _brief_result(brief_id="BRIEF_002", kind=BriefKind.JUDGED),
            _brief_result(brief_id="BRIEF_003"),
        )
    )
    text = render_scorecard_md(sc)
    assert "BRIEF_001" in text
    assert "BRIEF_002" in text
    assert "BRIEF_003" in text


@pytest.mark.unit
def test_markdown_omits_judge_section_when_no_calibrations() -> None:
    sc = _scorecard((_brief_result(),))
    text = render_scorecard_md(sc)
    assert "Judge calibration" not in text


@pytest.mark.unit
def test_markdown_includes_judge_section_when_calibrations_present() -> None:
    judge = JudgeCalibrationReport(
        rubric_id="summarise",
        spearman_rho=0.92,
        gate=0.7,
        passed=True,
        anchor_count=10,
    )
    briefs = (_brief_result(),)
    aggregated_events: dict[str, int] = {}
    sc = Scorecard(
        generated_at=datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC),
        company_config_path="r.yaml",
        cassette_path="c.json",
        cassette_sha256="0" * 64,
        suite_version="s",
        briefs=briefs,
        process_facts=AggregatedProcessFacts(
            total_events=0, events_by_class=aggregated_events
        ),
        judge_calibrations=(judge,),
    )
    text = render_scorecard_md(sc)
    assert "Judge calibration" in text
    assert "summarise" in text


@pytest.mark.unit
def test_failing_total_reports_fail() -> None:
    # Three briefs scoring 30 each -> total 90 / 300 = 30% < 65%.
    sc = _scorecard(
        (
            _brief_result(brief_id="BRIEF_A", grade=30, deduction=0),
            _brief_result(brief_id="BRIEF_B", grade=30, deduction=0),
            _brief_result(brief_id="BRIEF_C", grade=30, deduction=0),
        )
    )
    assert sc.is_passing is False
    assert "FAIL" in render_scorecard_md(sc)
