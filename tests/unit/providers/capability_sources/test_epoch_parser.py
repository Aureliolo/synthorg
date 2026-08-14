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


#: The feed's own marker for a row Epoch evaluated itself. Anything else in
#: this column is another leaderboard's number or a vendor's own report.
_MEASURED = "Epoch evaluations"


def _row(
    *,
    model_version: str = "vendor.model-a:1",
    performance: str = "0.605",
    benchmark: str = "MMLU",
    released: str = "2026-01-15",
    source: str = _MEASURED,
) -> str:
    return (
        f"m1,b1,{performance},{benchmark},2025-01-31,False,Display Name,"
        f"{model_version},Display Name,Display Name,,,{released},{source}"
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

    def test_as_of_records_the_read_because_the_feed_dates_nothing(self) -> None:
        """The feed's ``date`` column is the model's release date.

        Every model carries one date across every benchmark it appears on,
        and some are dated before the benchmark scoring them existed. Using
        it made evidence age read as model age, so the read is stamped
        instead: a claim that can be stood behind.
        """
        parsed = parse_epoch_csv(
            _csv(_row(released="2024-03-04")),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert parsed.scores[0].as_of == _INGESTED
        assert parsed.scores[0].ingested_at == _INGESTED


class TestProvenance:
    def test_only_rows_epoch_evaluated_itself_are_admitted(self) -> None:
        """A vendor grading its own model is the evidence being replaced."""
        parsed = parse_epoch_csv(
            _csv(
                _row(model_version="measured-model"),
                # The admitted value is matched exactly, so what a rejected
                # row names is immaterial: a vendor's own write-up, someone
                # else's leaderboard and a blank all fail the same test.
                _row(
                    model_version="self-reported",
                    source="Example Vendor Technical Report",
                ),
                _row(model_version="restated", source="Example Leaderboards"),
                _row(model_version="unattributed", source=""),
            ),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert [str(s.model_identifier) for s in parsed.scores] == ["measured-model"]
        assert parsed.rows_skipped == 3

    def test_the_provenance_match_is_exact_not_a_substring(self) -> None:
        """The column is free text naming papers and other leaderboards.

        A prefix or substring match would admit whatever happened to
        contain the marker, which is what the filter exists to refuse.
        """
        parsed = parse_epoch_csv(
            _csv(_row(source="Not Epoch evaluations at all")),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert parsed.scores == ()

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

    def test_the_release_date_column_never_reaches_a_score(self) -> None:
        """Two rows dated years apart still carry the read time.

        The column is the model's release date, so letting it vary ``as_of``
        would date the evidence by how old the MODEL is.
        """
        parsed = parse_epoch_csv(
            _csv(
                _row(benchmark="SWE-bench Verified", released="2025-02-01"),
                _row(benchmark="GPQA diamond", released="2026-06-01"),
            ),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert {s.as_of for s in parsed.scores} == {_INGESTED}

    def test_an_unknown_benchmark_is_skipped_rather_than_filed_under_a_guess(
        self,
    ) -> None:
        """Defaulting it into an axis moves every model's rank on that axis.

        The axis is ranked as a cohort and its members averaged, so a
        misfiled row is not an inert extra data point. A skipped one is a
        gap the source's counters report; a guessed one is a corruption
        nothing reports at all.
        """
        parsed = parse_epoch_csv(
            _csv(_row(benchmark="Some Benchmark Invented Next Year")),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert parsed.scores == ()
        assert parsed.rows_skipped == 1

    def test_a_configuration_suffix_is_skipped_not_bound(self) -> None:
        """``model_high`` names a run setting, and binds to nothing here.

        Reasoning effort is a per-task dial in this product, so no one
        setting is the one a model would be called with. Keeping the row
        would grade nothing while still occupying a cohort slot.
        """
        parsed = parse_epoch_csv(
            _csv(
                _row(model_version="model-y_high"),
                _row(model_version="model-y_32k"),
                _row(model_version="model-y_promax"),
                _row(model_version="model-y_thinking"),
                _row(model_version="model-y_unknown"),
                _row(model_version="model-y"),
            ),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert [str(s.model_identifier) for s in parsed.scores] == ["model-y"]
        assert parsed.rows_skipped == 5

    @pytest.mark.parametrize(
        "model_version",
        ["phi-1_5", "open_llama_7b", "Qwen-1_8B", "model_v2"],
    )
    def test_an_underscore_in_a_real_model_name_is_not_a_configuration(
        self, model_version: str
    ) -> None:
        """A version, a parameter count and a plain underscore all survive.

        The filter enumerates the settings the feed publishes rather than
        treating any trailing segment as one, because dropping ``_7b``
        would discard a model on the strength of its own name.
        """
        parsed = parse_epoch_csv(
            _csv(_row(model_version=model_version)),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert [str(s.model_identifier) for s in parsed.scores] == [model_version]


class TestSkippedRows:
    @pytest.mark.parametrize(
        "row",
        [
            _row(model_version=""),
            _row(performance="not-a-number"),
            _row(performance="1.5"),
            _row(performance=""),
            _row(source=""),
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
