"""Tests for the lexical text-similarity helpers."""

import pytest

from synthorg.core.text_similarity import (
    cosine_word_similarity,
    split_words,
    tokenize_words,
    word_overlap,
)


@pytest.mark.unit
class TestSplitWords:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("", []),
            ("Hello World", ["hello", "world"]),
            ("  a   B  c ", ["a", "b", "c"]),
            ("Dup dup", ["dup", "dup"]),
        ],
    )
    def test_split_words(self, text: str, expected: list[str]) -> None:
        assert split_words(text) == expected


@pytest.mark.unit
class TestTokenizeWords:
    def test_dedupes_and_lowercases(self) -> None:
        assert tokenize_words("The the THE cat") == frozenset({"the", "cat"})

    def test_empty(self) -> None:
        assert tokenize_words("   ") == frozenset()


@pytest.mark.unit
class TestWordOverlap:
    def test_empty_reference_is_zero(self) -> None:
        assert word_overlap(frozenset({"a"}), frozenset()) == 0.0

    def test_full_coverage(self) -> None:
        assert word_overlap(frozenset({"a", "b", "c"}), frozenset({"a", "b"})) == 1.0

    def test_partial_coverage(self) -> None:
        assert word_overlap(frozenset({"a"}), frozenset({"a", "b"})) == 0.5

    def test_asymmetric(self) -> None:
        a = frozenset({"a", "b", "c", "d"})
        b = frozenset({"a", "b"})
        assert word_overlap(a, b) == 1.0
        assert word_overlap(b, a) == 0.5


@pytest.mark.unit
class TestCosineWordSimilarity:
    def test_either_empty_is_zero(self) -> None:
        assert cosine_word_similarity("", "a b") == 0.0
        assert cosine_word_similarity("a b", "") == 0.0

    def test_identical(self) -> None:
        assert cosine_word_similarity("a b c", "c b a") == pytest.approx(1.0)

    def test_disjoint(self) -> None:
        assert cosine_word_similarity("a b", "c d") == 0.0

    def test_partial(self) -> None:
        # |{a}| / sqrt(2 * 2) = 1 / 2 = 0.5
        assert cosine_word_similarity("a b", "a c") == pytest.approx(0.5)
