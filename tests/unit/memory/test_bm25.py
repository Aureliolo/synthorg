"""Unit tests for Okapi BM25 scoring over the SQL inverted index."""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.memory.bm25 import (
    inverse_document_frequency,
    normalise_scores,
    score_document,
    term_frequencies,
    tokenize_for_index,
)

pytestmark = pytest.mark.unit


class TestTokenization:
    """The write path and query path must agree on terms."""

    def test_tokenize_lowercases_and_splits(self) -> None:
        assert tokenize_for_index("Deploy_The Rollback") == (
            "deploy",
            "rollback",
        )

    def test_tokenize_drops_stop_words(self) -> None:
        assert "the" not in tokenize_for_index("the rollback")

    def test_tokenize_empty_text_yields_no_terms(self) -> None:
        assert tokenize_for_index("   ") == ()

    def test_term_frequencies_counts_repeats(self) -> None:
        assert term_frequencies("rollback rollback deploy") == {
            NotBlankStr("rollback"): 2,
            NotBlankStr("deploy"): 1,
        }

    def test_term_frequencies_empty_text_is_empty(self) -> None:
        assert term_frequencies("") == {}


class TestInverseDocumentFrequency:
    """IDF must reward rare terms and never punish common ones."""

    def test_rare_term_scores_above_common_term(self) -> None:
        rare = inverse_document_frequency(doc_count=100, doc_frequency=1)
        common = inverse_document_frequency(doc_count=100, doc_frequency=90)
        assert rare > common

    def test_ubiquitous_term_is_non_negative(self) -> None:
        # The classic BM25 IDF goes negative once df > N/2; the +1 inside
        # the logarithm is what keeps a term present in every document
        # from actively penalising a match.
        assert inverse_document_frequency(doc_count=10, doc_frequency=10) >= 0.0

    def test_unseen_term_is_finite(self) -> None:
        assert inverse_document_frequency(doc_count=10, doc_frequency=0) > 0.0


class TestScoreDocument:
    """Scoring behaviour that retrieval quality depends on."""

    def test_no_matches_scores_zero(self) -> None:
        assert (
            score_document(
                matched=(),
                doc_length=10,
                doc_count=5,
                doc_frequencies={},
                avg_length=10.0,
            )
            == 0.0
        )

    def test_empty_corpus_scores_zero(self) -> None:
        assert (
            score_document(
                matched=((NotBlankStr("rollback"), 1),),
                doc_length=10,
                doc_count=0,
                doc_frequencies={NotBlankStr("rollback"): 0},
                avg_length=0.0,
            )
            == 0.0
        )

    def test_more_matches_scores_higher(self) -> None:
        frequencies = {NotBlankStr("rollback"): 2, NotBlankStr("deploy"): 2}
        one = score_document(
            matched=((NotBlankStr("rollback"), 1),),
            doc_length=10,
            doc_count=10,
            doc_frequencies=frequencies,
            avg_length=10.0,
        )
        two = score_document(
            matched=((NotBlankStr("rollback"), 1), (NotBlankStr("deploy"), 1)),
            doc_length=10,
            doc_count=10,
            doc_frequencies=frequencies,
            avg_length=10.0,
        )
        assert two > one

    def test_shorter_document_scores_higher_for_same_match(self) -> None:
        frequencies = {NotBlankStr("rollback"): 1}
        short = score_document(
            matched=((NotBlankStr("rollback"), 1),),
            doc_length=5,
            doc_count=10,
            doc_frequencies=frequencies,
            avg_length=50.0,
        )
        long = score_document(
            matched=((NotBlankStr("rollback"), 1),),
            doc_length=500,
            doc_count=10,
            doc_frequencies=frequencies,
            avg_length=50.0,
        )
        assert short > long

    def test_term_frequency_saturates(self) -> None:
        # BM25's defining property versus raw counts: the tenth
        # occurrence must add far less than the second, so a keyword
        # -stuffed memory cannot dominate recall.
        frequencies = {NotBlankStr("rollback"): 1}

        def at(count: int) -> float:
            return score_document(
                matched=((NotBlankStr("rollback"), count),),
                doc_length=100,
                doc_count=10,
                doc_frequencies=frequencies,
                avg_length=100.0,
            )

        first_gain = at(2) - at(1)
        later_gain = at(10) - at(9)
        assert later_gain < first_gain

    def test_zero_average_length_does_not_divide_by_zero(self) -> None:
        assert (
            score_document(
                matched=((NotBlankStr("rollback"), 1),),
                doc_length=0,
                doc_count=3,
                doc_frequencies={NotBlankStr("rollback"): 1},
                avg_length=0.0,
            )
            > 0.0
        )


class TestNormaliseScores:
    """Normalisation must fit the [0, 1] relevance_score constraint."""

    def test_empty_input(self) -> None:
        assert normalise_scores(()) == ()

    def test_spread_maps_to_unit_range(self) -> None:
        assert normalise_scores((10.0, 5.0, 0.0)) == (1.0, 0.5, 0.0)

    def test_all_equal_maps_to_max_not_zero(self) -> None:
        # Every result is equally and maximally relevant within the set;
        # mapping them to 0.0 would make a strong uniform result set look
        # worthless to any downstream threshold.
        assert normalise_scores((3.0, 3.0)) == (1.0, 1.0)

    def test_single_score_maps_to_max(self) -> None:
        assert normalise_scores((7.0,)) == (1.0,)

    def test_output_within_relevance_score_bounds(self) -> None:
        for value in normalise_scores((0.1, 99.0, 4.2, 0.0)):
            assert 0.0 <= value <= 1.0
