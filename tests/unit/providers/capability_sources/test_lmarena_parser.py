"""Unit tests for the LMArena leaderboard-parquet parser.

The fixtures build real parquet bytes rather than mocking pyarrow, so a
column-name or dtype change upstream fails here instead of producing
silently-empty evidence.
"""

import io
from collections.abc import Sequence
from datetime import UTC, datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from synthorg.providers.capability_sources.errors import CapabilitySourceParseError
from synthorg.providers.capability_sources.parsers.lmarena import (
    parse_lmarena_parquet,
)

pytestmark = pytest.mark.unit

_INGESTED = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
_LABEL = "lmarena"
_AUGUST = "2026-08-12"
_JULY = "2026-07-01"

#: Enough votes to clear the parser's confidence floor in every fixture
#: that is not deliberately testing that floor.
_VOTES = 5_000.0


def _parquet(
    *,
    model_name: list[str] | None = None,
    rating: list[float] | None = None,
    category: list[str] | None = None,
    published: list[str] | None = None,
    vote_count: list[float] | None = None,
    drop: str = "",
) -> bytes:
    """Build a parquet document with the feed's real column shape."""
    columns: dict[str, Sequence[object]] = {
        "model_name": model_name if model_name is not None else ["vendor-model-a"],
        "organization": ["an-org"],
        "license": ["Proprietary"],
        "rating": rating if rating is not None else [1275.0],
        "rating_lower": [1260.0],
        "rating_upper": [1290.0],
        "variance": [40.0],
        "vote_count": vote_count if vote_count is not None else [_VOTES],
        "rank": [1.0],
        "category": category if category is not None else ["overall"],
        "leaderboard_publish_date": published if published is not None else [_AUGUST],
    }
    rows = len(columns["model_name"])
    for name, values in columns.items():
        if len(values) == 1 and rows > 1:
            columns[name] = list(values) * rows
    if drop:
        del columns[drop]
    buffer = io.BytesIO()
    pq.write_table(pa.table(columns), buffer)
    return buffer.getvalue()


class TestShape:
    @pytest.mark.parametrize(
        "column",
        ["model_name", "rating", "category", "leaderboard_publish_date", "vote_count"],
    )
    def test_a_missing_column_fails_loudly(self, column: str) -> None:
        with pytest.raises(CapabilitySourceParseError, match=column):
            parse_lmarena_parquet(
                _parquet(drop=column),
                source_label=_LABEL,
                ingested_at=_INGESTED,
            )

    def test_a_document_that_is_not_parquet_fails_loudly(self) -> None:
        """A fetched HTML error page must fail, never parse to nothing."""
        with pytest.raises(CapabilitySourceParseError, match="parquet"):
            parse_lmarena_parquet(
                b"<html>404</html>",
                source_label=_LABEL,
                ingested_at=_INGESTED,
            )


class TestAxisAllowlist:
    @pytest.mark.parametrize(
        ("category", "axis"),
        [
            ("coding", "coding"),
            ("webdev", "coding"),
            ("webdev-react", "coding"),
            ("math", "reasoning"),
            ("hard_prompts", "reasoning"),
            ("expert", "reasoning"),
            ("industry_mathematical", "reasoning"),
            ("overall", "general"),
            ("instruction_following", "general"),
            ("creative_writing", "general"),
        ],
    )
    def test_a_task_board_lands_on_its_axis(self, category: str, axis: str) -> None:
        parsed = parse_lmarena_parquet(
            _parquet(category=[category]),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert [s.axis for s in parsed.scores] == [axis]

    @pytest.mark.parametrize(
        "category",
        ["french", "chinese", "korean", "non_english", "exclude_ties"],
    )
    def test_a_language_or_methodology_board_does_not_contribute(
        self, category: str
    ) -> None:
        """Averaging nine language slices would weight multilingual nine times.

        These are the same votes cut a different way, not a distinct
        skill, so they are counted as skipped rather than folded into an
        axis.
        """
        parsed = parse_lmarena_parquet(
            _parquet(category=[category]),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert parsed.scores == ()
        assert parsed.rows_read == 1
        assert parsed.rows_skipped == 1

    def test_an_ungraded_board_does_not_suppress_a_graded_one(self) -> None:
        parsed = parse_lmarena_parquet(
            _parquet(
                model_name=["m1", "m1"],
                rating=[1275.0, 1500.0],
                category=["french", "coding"],
                published=[_AUGUST, _AUGUST],
                vote_count=[_VOTES, _VOTES],
            ),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert [s.axis for s in parsed.scores] == ["coding"]
        assert parsed.rows_skipped == 1


class TestNormalisation:
    def test_the_band_floor_is_zero_and_the_ceiling_is_one_hundred(self) -> None:
        parsed = parse_lmarena_parquet(
            _parquet(
                model_name=["low", "high"],
                rating=[900.0, 1650.0],
                category=["overall", "overall"],
                published=[_AUGUST, _AUGUST],
                vote_count=[_VOTES, _VOTES],
            ),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        by_model = {str(s.model_identifier): s.score for s in parsed.scores}
        assert by_model["low"] == pytest.approx(0.0)
        assert by_model["high"] == pytest.approx(100.0)

    def test_a_midpoint_rating_lands_midway(self) -> None:
        parsed = parse_lmarena_parquet(
            _parquet(rating=[1275.0]),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert parsed.scores[0].score == pytest.approx(50.0)

    def test_a_rating_beyond_the_band_is_held_at_the_edge(self) -> None:
        """A rating past the band is a real measurement, not a bad row.

        It says "at or beyond this end of the scale", so it is kept at the
        edge rather than discarded the way an unparseable value is.
        """
        parsed = parse_lmarena_parquet(
            _parquet(
                model_name=["under", "over"],
                rating=[534.0, 1900.0],
                category=["overall", "overall"],
                published=[_AUGUST, _AUGUST],
                vote_count=[_VOTES, _VOTES],
            ),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        by_model = {str(s.model_identifier): s.score for s in parsed.scores}
        assert by_model["under"] == pytest.approx(0.0)
        assert by_model["over"] == pytest.approx(100.0)
        assert parsed.rows_skipped == 0

    def test_the_band_does_not_move_with_the_feed(self) -> None:
        """A min-max would re-grade every model the day a weak one landed."""
        strong_alone = parse_lmarena_parquet(
            _parquet(rating=[1500.0]),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        with_a_weak_newcomer = parse_lmarena_parquet(
            _parquet(
                model_name=["vendor-model-a", "newcomer"],
                rating=[1500.0, 950.0],
                category=["overall", "overall"],
                published=[_AUGUST, _AUGUST],
                vote_count=[_VOTES, _VOTES],
            ),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        after = {str(s.model_identifier): s.score for s in with_a_weak_newcomer.scores}
        assert after["vendor-model-a"] == pytest.approx(strong_alone.scores[0].score)


class TestAggregation:
    def test_boards_on_one_axis_average(self) -> None:
        parsed = parse_lmarena_parquet(
            _parquet(
                model_name=["m1", "m1"],
                rating=[900.0, 1650.0],
                category=["math", "expert"],
                published=[_AUGUST, _AUGUST],
                vote_count=[_VOTES, _VOTES],
            ),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert len(parsed.scores) == 1
        assert parsed.scores[0].axis == "reasoning"
        assert parsed.scores[0].score == pytest.approx(50.0)

    def test_models_are_kept_apart(self) -> None:
        parsed = parse_lmarena_parquet(
            _parquet(
                model_name=["m1", "m2"],
                rating=[1650.0, 900.0],
                category=["coding", "coding"],
                published=[_AUGUST, _AUGUST],
                vote_count=[_VOTES, _VOTES],
            ),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        by_model = {str(s.model_identifier): s.score for s in parsed.scores}
        assert by_model == {"m1": pytest.approx(100.0), "m2": pytest.approx(0.0)}

    def test_as_of_is_the_newest_publication_in_the_group(self) -> None:
        parsed = parse_lmarena_parquet(
            _parquet(
                model_name=["m1", "m1"],
                rating=[1275.0, 1275.0],
                category=["math", "expert"],
                published=[_JULY, _AUGUST],
                vote_count=[_VOTES, _VOTES],
            ),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert parsed.scores[0].as_of == datetime(2026, 8, 12, tzinfo=UTC)

    def test_provenance_travels_with_every_score(self) -> None:
        parsed = parse_lmarena_parquet(
            _parquet(), source_label=_LABEL, ingested_at=_INGESTED
        )
        assert str(parsed.scores[0].source_label) == _LABEL
        assert parsed.scores[0].ingested_at == _INGESTED


class TestSkippedRows:
    def test_a_thinly_voted_row_is_skipped(self) -> None:
        """Below the floor a rating is noise, and the board says so itself."""
        parsed = parse_lmarena_parquet(
            _parquet(vote_count=[3.0]),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert parsed.scores == ()
        assert parsed.rows_skipped == 1

    def test_a_blank_model_is_skipped(self) -> None:
        parsed = parse_lmarena_parquet(
            _parquet(model_name=["  "]),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert parsed.scores == ()
        assert parsed.rows_skipped == 1

    def test_an_unparseable_date_is_skipped(self) -> None:
        parsed = parse_lmarena_parquet(
            _parquet(published=["not-a-date"]),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert parsed.scores == ()
        assert parsed.rows_skipped == 1

    def test_one_bad_row_does_not_discard_the_good_ones(self) -> None:
        parsed = parse_lmarena_parquet(
            _parquet(
                model_name=["good", "  "],
                rating=[1650.0, 1650.0],
                category=["coding", "coding"],
                published=[_AUGUST, _AUGUST],
                vote_count=[_VOTES, _VOTES],
            ),
            source_label=_LABEL,
            ingested_at=_INGESTED,
        )
        assert len(parsed.scores) == 1
        assert parsed.scores[0].score == pytest.approx(100.0)
        assert parsed.rows_read == 2
        assert parsed.rows_skipped == 1
