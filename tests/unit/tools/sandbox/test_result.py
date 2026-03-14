"""Tests for SandboxResult model."""

import pytest
from pydantic import ValidationError

from synthorg.tools.sandbox.result import SandboxResult

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


class TestSandboxResult:
    """SandboxResult is frozen with a computed ``success`` field."""

    @pytest.mark.parametrize(
        ("returncode", "timed_out", "expected_success"),
        [
            (0, False, True),
            (1, False, False),
            (0, True, False),
            (-1, True, False),
        ],
        ids=["zero-no-timeout", "nonzero", "timeout-zero", "timeout-neg"],
    )
    def test_success_matrix(
        self,
        returncode: int,
        timed_out: bool,
        expected_success: bool,
    ) -> None:
        result = SandboxResult(
            stdout="",
            stderr="",
            returncode=returncode,
            timed_out=timed_out,
        )
        assert result.success is expected_success

    def test_frozen(self) -> None:
        result = SandboxResult(
            stdout="ok",
            stderr="",
            returncode=0,
        )
        with pytest.raises(ValidationError):
            result.stdout = "modified"  # type: ignore[misc]

    def test_timed_out_defaults_false(self) -> None:
        result = SandboxResult(
            stdout="",
            stderr="",
            returncode=0,
        )
        assert result.timed_out is False

    def test_negative_returncode(self) -> None:
        result = SandboxResult(
            stdout="",
            stderr="signal",
            returncode=-9,
        )
        assert result.success is False
