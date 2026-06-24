"""Unit tests for the setup company-resume read helpers."""

import pytest

from synthorg.api.controllers.setup._company_read import (
    _department_count,
    _normalize_profile,
    _parse_budget,
)


@pytest.mark.unit
class TestDepartmentCount:
    """``_department_count`` tolerates absent / corrupt JSON blobs."""

    def test_counts_a_valid_list(self) -> None:
        assert _department_count('[{"name": "eng"}, {"name": "ops"}]') == 2

    def test_blank_is_zero(self) -> None:
        assert _department_count(None) == 0
        assert _department_count("") == 0

    def test_malformed_json_is_zero(self) -> None:
        assert _department_count("not-json") == 0

    def test_non_list_json_is_zero(self) -> None:
        assert _department_count('{"name": "eng"}') == 0


@pytest.mark.unit
class TestParseBudget:
    """``_parse_budget`` coerces the persisted string or degrades to None."""

    def test_parses_a_number(self) -> None:
        assert _parse_budget("500") == 500.0
        assert _parse_budget("12.5") == 12.5

    def test_blank_is_none(self) -> None:
        assert _parse_budget(None) is None
        assert _parse_budget("") is None

    def test_non_numeric_is_none(self) -> None:
        assert _parse_budget("free") is None


@pytest.mark.unit
class TestNormalizeProfile:
    """``_normalize_profile`` coerces to a known tier profile, default balanced."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("economy", "economy"),
            ("premium", "premium"),
            ("balanced", "balanced"),
            (None, "balanced"),
            ("nonsense", "balanced"),
            ("", "balanced"),
        ],
    )
    def test_coercion(self, raw: str | None, expected: str) -> None:
        assert _normalize_profile(raw) == expected
