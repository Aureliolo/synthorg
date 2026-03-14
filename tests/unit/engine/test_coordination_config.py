"""Tests for CoordinationConfig."""

import pytest
from pydantic import ValidationError

from synthorg.engine.coordination.config import CoordinationConfig


class TestCoordinationConfig:
    """CoordinationConfig model tests."""

    @pytest.mark.unit
    def test_defaults(self) -> None:
        """Default config uses sensible values."""
        cfg = CoordinationConfig()
        assert cfg.max_concurrency_per_wave is None
        assert cfg.fail_fast is False
        assert cfg.enable_workspace_isolation is True
        assert cfg.base_branch == "main"

    @pytest.mark.unit
    def test_custom_values(self) -> None:
        """All fields can be customized."""
        cfg = CoordinationConfig(
            max_concurrency_per_wave=4,
            fail_fast=True,
            enable_workspace_isolation=False,
            base_branch="develop",
        )
        assert cfg.max_concurrency_per_wave == 4
        assert cfg.fail_fast is True
        assert cfg.enable_workspace_isolation is False
        assert cfg.base_branch == "develop"

    @pytest.mark.unit
    def test_frozen(self) -> None:
        """Config is immutable."""
        cfg = CoordinationConfig()
        with pytest.raises(ValidationError):
            cfg.fail_fast = True  # type: ignore[misc]

    @pytest.mark.unit
    def test_max_concurrency_must_be_positive(self) -> None:
        """max_concurrency_per_wave must be >= 1."""
        with pytest.raises(ValidationError, match="greater than or equal to 1"):
            CoordinationConfig(max_concurrency_per_wave=0)

    @pytest.mark.unit
    def test_extra_fields_forbidden(self) -> None:
        """Extra fields are rejected."""
        with pytest.raises(ValidationError, match="Extra inputs"):
            CoordinationConfig(unknown_field="value")  # type: ignore[call-arg]

    @pytest.mark.unit
    def test_base_branch_not_blank(self) -> None:
        """base_branch must not be blank."""
        with pytest.raises(ValidationError):
            CoordinationConfig(base_branch="   ")
