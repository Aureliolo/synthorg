# module-kind: tests
"""Unit tests for the shared org-fact query-term extraction."""

import pytest

from synthorg.persistence._org_fact_query import (
    build_term_match_sql,
    like_contains_pattern,
    org_query_terms,
)

pytestmark = pytest.mark.unit


class TestOrgQueryTerms:
    def test_splits_a_composed_query_into_salient_terms(self) -> None:
        terms = org_query_terms("checkout resilience. Harden the checkout flow.")
        # De-duplicated, lowercased, stopword ("the") and the short word
        # dropped; "checkout" appears once despite two occurrences.
        assert terms == ("checkout", "resilience", "harden", "flow")

    def test_drops_short_terms_and_stopwords(self) -> None:
        assert org_query_terms("a to be the and of it") == ()

    def test_preserves_first_seen_order_and_caps_the_count(self) -> None:
        text = " ".join(f"term{i:02d}word" for i in range(20))
        terms = org_query_terms(text)
        assert len(terms) == 12
        assert terms[0] == "term00word"

    def test_is_case_insensitive_and_deduplicates(self) -> None:
        assert org_query_terms("Checkout CHECKOUT checkout") == ("checkout",)

    def test_empty_text_yields_no_terms(self) -> None:
        assert org_query_terms("   ...  ") == ()
        assert org_query_terms("") == ()


class TestLikeContainsPattern:
    def test_wraps_value_as_a_contains_pattern(self) -> None:
        assert like_contains_pattern("checkout") == "%checkout%"

    def test_escapes_like_metacharacters(self) -> None:
        # A term carrying % or _ must match literally, not as a wildcard.
        assert like_contains_pattern("a%b_c") == r"%a\%b\_c%"

    def test_escapes_the_escape_character_itself(self) -> None:
        assert like_contains_pattern("a\\b") == "%a\\\\b%"


class TestBuildTermMatchSql:
    def test_builds_or_where_and_match_count_order_for_sqlite(self) -> None:
        where, order, patterns = build_term_match_sql(
            ("checkout", "resilience"), placeholder="?", int_cast=""
        )
        assert where == (
            "(content_normalized LIKE ? ESCAPE '\\' "
            "OR content_normalized LIKE ? ESCAPE '\\')"
        )
        assert order.startswith(
            "((content_normalized LIKE ? ESCAPE '\\') "
            "+ (content_normalized LIKE ? ESCAPE '\\')) DESC"
        )
        assert patterns == ["%checkout%", "%resilience%"]

    def test_casts_booleans_to_int_for_postgres(self) -> None:
        _where, order, _patterns = build_term_match_sql(
            ("checkout",), placeholder="%s", int_cast="::int"
        )
        assert "(content_normalized LIKE %s ESCAPE '\\')::int" in order
