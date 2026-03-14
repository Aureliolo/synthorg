"""Tests for shared validation utilities."""

import pytest

from ai_company.core.validation import is_valid_action_type

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


class TestIsValidActionType:
    """is_valid_action_type() validates category:action format."""

    @pytest.mark.parametrize(
        "valid",
        [
            "deploy:production",
            "db:admin",
            "comms:internal",
            "test:action",
            "a:b",
        ],
    )
    def test_valid_formats(self, valid: str) -> None:
        assert is_valid_action_type(valid) is True

    @pytest.mark.parametrize(
        "invalid",
        [
            "deploy",
            ":release",
            "deploy:",
            "deploy:  ",
            "  :release",
            "a:b:c",
            "",
            "   ",
            "no-colon-at-all",
        ],
    )
    def test_invalid_formats(self, invalid: str) -> None:
        assert is_valid_action_type(invalid) is False
