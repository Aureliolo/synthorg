"""Tests for seniority levels and seniority comparison."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from synthorg.hr.seniority import SeniorityLevel, compare_seniority

pytestmark = pytest.mark.unit

_seniority_levels = st.sampled_from(SeniorityLevel)


class TestSeniorityLevel:
    def test_seniority_level_has_8_members(self) -> None:
        assert len(SeniorityLevel) == 8

    def test_seniority_levels_are_lowercase(self) -> None:
        for member in SeniorityLevel:
            assert member.value == member.value.lower()


class TestCompareSeniority:
    def test_higher_is_positive(self) -> None:
        assert compare_seniority(SeniorityLevel.C_SUITE, SeniorityLevel.JUNIOR) > 0

    def test_lower_is_negative(self) -> None:
        assert compare_seniority(SeniorityLevel.JUNIOR, SeniorityLevel.SENIOR) < 0

    def test_equal_is_zero(self) -> None:
        assert compare_seniority(SeniorityLevel.LEAD, SeniorityLevel.LEAD) == 0

    def test_adjacent_levels(self) -> None:
        assert compare_seniority(SeniorityLevel.MID, SeniorityLevel.JUNIOR) > 0
        assert compare_seniority(SeniorityLevel.SENIOR, SeniorityLevel.MID) > 0

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            (SeniorityLevel.VP, SeniorityLevel.DIRECTOR),
            (SeniorityLevel.C_SUITE, SeniorityLevel.VP),
            (SeniorityLevel.PRINCIPAL, SeniorityLevel.LEAD),
        ],
    )
    def test_ordering_pairs(self, a: SeniorityLevel, b: SeniorityLevel) -> None:
        assert compare_seniority(a, b) > 0
        assert compare_seniority(b, a) < 0


class TestCompareSeniorityProperties:
    @given(a=_seniority_levels)
    def test_reflexive_zero(self, a: SeniorityLevel) -> None:
        assert compare_seniority(a, a) == 0

    @given(a=_seniority_levels, b=_seniority_levels)
    def test_anti_symmetry(self, a: SeniorityLevel, b: SeniorityLevel) -> None:
        assert compare_seniority(a, b) == -compare_seniority(b, a)

    @given(
        a=_seniority_levels,
        b=_seniority_levels,
        c=_seniority_levels,
    )
    def test_transitivity(
        self,
        a: SeniorityLevel,
        b: SeniorityLevel,
        c: SeniorityLevel,
    ) -> None:
        ab = compare_seniority(a, b)
        bc = compare_seniority(b, c)
        ac = compare_seniority(a, c)
        if ab >= 0 and bc >= 0:
            assert ac >= 0
        if ab <= 0 and bc <= 0:
            assert ac <= 0

    @given(a=_seniority_levels, b=_seniority_levels)
    def test_totality(self, a: SeniorityLevel, b: SeniorityLevel) -> None:
        result = compare_seniority(a, b)
        assert isinstance(result, int)
        if a == b:
            assert result == 0
        else:
            assert result != 0
