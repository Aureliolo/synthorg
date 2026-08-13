"""Unit tests for the Epoch AI benchmark CSV parser.

The header and sample rows here are taken verbatim from the published
feed, so a shape change upstream shows up as a failing test rather than as
silently-empty evidence.
"""

from datetime import UTC, datetime

import pytest

from synthorg.providers.capability_sources.errors import CapabilitySourceParseError
from synthorg.providers.capability_sources.parsers.epoch import parse_epoch_csv

pytestmark = pytest.mark.unit

_INGESTED = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
_LABEL = "epoch-ai"

_HEADER = (
    "model_id,benchmark_id,performance,benchmark,benchmark_release_date,"
    "optimized,model,model_version,Model,model_group,Model aggregation,"
    "Model Aggregation Date,date,source"
)


def _row(
    *,
    model_version: str = "vendor.model-a:1",
    performance: str = "0.605",
    benchmark: str = "MMLU",
    measured: str = "2026-01-15",
) -> str:
    return (
        f"m1,b1,{performance},{benchmark},2025-01-31,False,Display Name,"
        f"{model_version},Display Name,Display Name,,,{measured},a source"
    )


def _csv(*rows: str) -> str:
    return "\n".join((_HEADER, *rows)) + "\n"


class TestShape:
    def test_a_missing_column_fails_loudly(self) -> None:
        """A reshuffled feed must fail, never parse to nothing.

        Returning an empty result would read as "this source graded no
        models", which is what a working feed covering none of your
        models also looks like.
        """
        with pytest.raises(CapabilitySourceParseError, match="model_version"):
            parse_epoch_csv(
                "model_id,performance,benchmark,date\nm1,0.5,MMLU,2026-01-15\n",
                source_label=_LABEL,
                ingested_at=_INGESTED,
            )

    def test_an_empty_document_fails_loudly(self) -> None:
        with pytest.raises(CapabilitySourceParseError):
            parse_epoch_csv("", source_label=_LABEL, ingested_at=_INGESTED)

    def test_a_header_with_no_rows_parses_to_nothing(self) -> None:
        # Distinct from a broken feed: the shape is intact, there is just
        # nothing in it, so this is a healthy source with no evidence.
        parsed = parse_epoch_csv(_csv(), source_label=_LABEL, ingested_at=_INGESTED)
        assert parsed.scores == ()
        assert parsed.rows_read == 0


class TestScores:
    def test_a_fraction_becomes_a_percentage(self) -> None:
        parsed = parse_epoch_csv(
            _csv(_row(performance="0.605")),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert len(parsed.scores) == 1
        assert parsed.scores[0].score == pytest.approx(60.5)

    def test_the_vendor_model_id_is_kept_not_the_display_name(self) -> None:
        """The display name resolves to a configured pair only by guessing."""
        parsed = parse_epoch_csv(
            _csv(_row(model_version="vendor.model-a:1")),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert parsed.scores[0].model_identifier == "vendor.model-a:1"

    def test_the_measurement_date_becomes_as_of(self) -> None:
        """Staleness must reflect the measurement, not the download."""
        parsed = parse_epoch_csv(
            _csv(_row(measured="2026-01-15")),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert parsed.scores[0].as_of == datetime(2026, 1, 15, tzinfo=UTC)
        assert parsed.scores[0].ingested_at == _INGESTED

    def test_benchmarks_are_grouped_onto_axes(self) -> None:
        parsed = parse_epoch_csv(
            _csv(
                _row(benchmark="SWE-bench Verified", performance="0.4"),
                _row(benchmark="GPQA Diamond", performance="0.8"),
                _row(benchmark="MMLU", performance="0.9"),
            ),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        by_axis = {s.axis: s.score for s in parsed.scores}
        assert by_axis["coding"] == pytest.approx(40.0)
        assert by_axis["reasoning"] == pytest.approx(80.0)
        assert by_axis["general"] == pytest.approx(90.0)

    def test_several_benchmarks_on_one_axis_are_averaged(self) -> None:
        """The mean, so no single benchmark speaks for the whole axis."""
        parsed = parse_epoch_csv(
            _csv(
                _row(benchmark="SWE-bench Verified", performance="0.4"),
                _row(benchmark="Aider Polyglot", performance="0.6"),
            ),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert len(parsed.scores) == 1
        assert parsed.scores[0].axis == "coding"
        assert parsed.scores[0].score == pytest.approx(50.0)

    def test_an_axis_is_dated_by_its_newest_input(self) -> None:
        """Reporting the oldest would make an active model look abandoned."""
        parsed = parse_epoch_csv(
            _csv(
                _row(benchmark="SWE-bench Verified", measured="2025-02-01"),
                _row(benchmark="Aider Polyglot", measured="2026-06-01"),
            ),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert parsed.scores[0].as_of == datetime(2026, 6, 1, tzinfo=UTC)

    def test_an_unknown_benchmark_lands_on_general_rather_than_being_dropped(
        self,
    ) -> None:
        parsed = parse_epoch_csv(
            _csv(_row(benchmark="Some Benchmark Invented Next Year")),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert parsed.scores[0].axis == "general"


class TestSkippedRows:
    @pytest.mark.parametrize(
        "row",
        [
            _row(model_version=""),
            _row(performance="not-a-number"),
            _row(performance="1.5"),
            _row(measured=""),
            _row(measured="not-a-date"),
        ],
    )
    def test_an_unusable_row_is_counted_not_guessed_at(self, row: str) -> None:
        """An out-of-band or unparseable value yields nothing, never a guess.

        Clamping 1.5 to 100 would invent a plausible score from a column
        we have just proved we no longer understand.
        """
        parsed = parse_epoch_csv(_csv(row), source_label=_LABEL, ingested_at=_INGESTED)
        assert parsed.scores == ()
        assert parsed.rows_read == 1
        assert parsed.rows_skipped == 1

    def test_one_bad_row_does_not_discard_the_good_ones(self) -> None:
        parsed = parse_epoch_csv(
            _csv(
                _row(model_version="good-model"),
                _row(model_version=""),
            ),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert len(parsed.scores) == 1
        assert parsed.scores[0].model_identifier == "good-model"
        assert parsed.rows_read == 2
        assert parsed.rows_skipped == 1
