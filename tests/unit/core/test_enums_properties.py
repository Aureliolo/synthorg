"""Property-based tests for enum comparator algebraic properties."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from synthorg.core.enums import AutonomyLevel, compare_autonomy
from synthorg.hr.seniority import SeniorityLevel, compare_seniority

pytestmark = pytest.mark.unit

_seniority_levels = st.sampled_from(SeniorityLevel)
_autonomy_levels = st.sampled_from(AutonomyLevel)


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


class TestCompareAutonomyProperties:
    @given(a=_autonomy_levels)
    def test_reflexive_zero(self, a: AutonomyLevel) -> None:
        assert compare_autonomy(a, a) == 0

    @given(a=_autonomy_levels, b=_autonomy_levels)
    def test_anti_symmetry(self, a: AutonomyLevel, b: AutonomyLevel) -> None:
        assert compare_autonomy(a, b) == -compare_autonomy(b, a)

    @given(
        a=_autonomy_levels,
        b=_autonomy_levels,
        c=_autonomy_levels,
    )
    def test_transitivity(
        self,
        a: AutonomyLevel,
        b: AutonomyLevel,
        c: AutonomyLevel,
    ) -> None:
        ab = compare_autonomy(a, b)
        bc = compare_autonomy(b, c)
        ac = compare_autonomy(a, c)
        if ab >= 0 and bc >= 0:
            assert ac >= 0
        if ab <= 0 and bc <= 0:
            assert ac <= 0

    @given(a=_autonomy_levels, b=_autonomy_levels)
    def test_totality(self, a: AutonomyLevel, b: AutonomyLevel) -> None:
        result = compare_autonomy(a, b)
        assert isinstance(result, int)
        if a == b:
            assert result == 0
        else:
            assert result != 0
