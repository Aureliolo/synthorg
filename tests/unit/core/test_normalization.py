"""Tests for ``synthorg.core.normalization`` helpers."""

from dataclasses import dataclass

import pytest
from hypothesis import given
from hypothesis import strategies as st

from synthorg.core.normalization import find_by_name_ci, normalize_identifier


@pytest.mark.unit
class TestNormalizeIdentifier:
    """``normalize_identifier`` strips whitespace and case-folds."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("alice", "alice"),
            ("Alice", "alice"),
            ("  Alice ", "alice"),
            ("\tBob\n", "bob"),
            # German sharp-s: casefold() expands ß → ss; .lower() would not.
            ("Straße", "strasse"),
            ("STRASSE", "strasse"),
            # Greek capital sigma → lowercase sigma (final-sigma is preserved
            # in casefold; both ΣΊΓΜΑ and σίγμα fold to the same form).
            ("ΣΊΓΜΑ", "σίγμα"),
            # Turkish capital dotted-I (U+0130): Unicode default case-folding
            # produces 'i' followed by combining dot above (U+0307).  This
            # documents Python's locale-independent behaviour -- it is NOT
            # Turkish-locale-aware, but is consistent across platforms.
            ("İstanbul", "i̇stanbul"),
            # Empty / whitespace-only.
            ("", ""),
            ("   ", ""),
        ],
    )
    def test_normalize_identifier_variants(
        self,
        value: str,
        expected: str,
    ) -> None:
        assert normalize_identifier(value) == expected

    @given(value=st.text())
    def test_matches_strip_casefold_contract(self, value: str) -> None:
        """Pin the contract: behaviour must equal ``value.strip().casefold()``."""
        assert normalize_identifier(value) == value.strip().casefold()

    @given(value=st.text())
    def test_idempotent(self, value: str) -> None:
        """Applying the helper twice yields the same result as once."""
        once = normalize_identifier(value)
        assert normalize_identifier(once) == once


@pytest.mark.unit
class TestFindByNameCi:
    """``find_by_name_ci`` linear search."""

    @dataclass
    class Item:
        name: str

    def test_returns_first_match(self) -> None:
        items = (self.Item("Alice"), self.Item("Bob"))
        assert find_by_name_ci(items, "alice") is items[0]

    def test_returns_none_on_no_match(self) -> None:
        items = (self.Item("Alice"), self.Item("Bob"))
        assert find_by_name_ci(items, "eve") is None

    def test_handles_non_string_attr(self) -> None:
        @dataclass
        class Weird:
            name: int

        assert find_by_name_ci((Weird(name=1),), "1") is None

    def test_custom_name_attr(self) -> None:
        @dataclass
        class Dept:
            title: str

        items = (Dept(title="Engineering"),)
        assert find_by_name_ci(items, "engineering", name_attr="title") is items[0]

    def test_empty_iterable(self) -> None:
        assert find_by_name_ci((), "anything") is None

    def test_match_strips_and_casefolds_target(self) -> None:
        items = (self.Item("Alice"),)
        assert find_by_name_ci(items, "  ALICE  ") is items[0]
