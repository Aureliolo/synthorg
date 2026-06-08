"""Property-based tests for enum comparator algebraic properties."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from synthorg.core.autonomy_enums import AutonomyLevel, compare_autonomy

pytestmark = pytest.mark.unit

_autonomy_levels = st.sampled_from(AutonomyLevel)


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
