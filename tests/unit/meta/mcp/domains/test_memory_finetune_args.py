"""Invoker-boundary args tests for the memory fine-tune tools.

``MemoryStartFineTuneArgs`` / ``MemoryRunPreflightArgs`` are validated
against the raw ``arguments`` dict at the invoker boundary before the
handler runs (``extra="forbid"``). These tests pin the directory /
trajectory split there: directory mode requires ``source_dir``;
trajectory mode makes it optional. Without the field + validator a
trajectory caller would be rejected as an unknown-key / missing-field
error before ever reaching the handler-side parse.
"""

import pytest
from pydantic import ValidationError

from synthorg.memory.embedding.fine_tune_models import FineTuneDataSourceType
from synthorg.memory.fine_tune_plan import FineTunePlan
from synthorg.meta.mcp.domains._remaining_args._memory_finetune import (
    MemoryRunPreflightArgs,
    MemoryStartFineTuneArgs,
)
from synthorg.meta.mcp.errors import ArgumentValidationError

pytestmark = pytest.mark.unit


class TestToPlan:
    """The ``to_plan`` adapter bridges wire fields to ``FineTunePlan``."""

    def test_valid_directory_plan(self) -> None:
        args = MemoryRunPreflightArgs(source_dir="/data/org-docs")
        plan = args.to_plan()
        assert isinstance(plan, FineTunePlan)
        assert plan.data_source is FineTuneDataSourceType.DIRECTORY
        assert plan.source_dir == "/data/org-docs"

    def test_valid_trajectory_plan(self) -> None:
        args = MemoryRunPreflightArgs(data_source="trajectory")
        plan = args.to_plan()
        assert plan.data_source is FineTuneDataSourceType.TRAJECTORY
        assert plan.source_dir is None

    def test_path_traversal_wrapped_as_argument_error(self) -> None:
        # The wire model accepts any non-blank ``source_dir``; the
        # canonical ``FineTunePlan`` rejects parent-directory traversal,
        # and the adapter must surface that as a typed argument error.
        args = MemoryRunPreflightArgs(source_dir="../escape")
        with pytest.raises(ArgumentValidationError):
            args.to_plan()


class TestMemoryRunPreflightArgs:
    """Read-only preflight args (no admin guardrails)."""

    def test_directory_mode_with_source_dir(self) -> None:
        args = MemoryRunPreflightArgs(source_dir="/data/org-docs")
        assert args.data_source == "directory"
        assert args.source_dir == "/data/org-docs"

    def test_trajectory_mode_omits_source_dir(self) -> None:
        args = MemoryRunPreflightArgs(data_source="trajectory")
        assert args.data_source == "trajectory"
        assert args.source_dir is None

    def test_directory_mode_missing_source_dir_rejected(self) -> None:
        with pytest.raises(ValidationError) as info:
            MemoryRunPreflightArgs(data_source="directory")
        assert "source_dir is required" in str(info.value)

    def test_implicit_directory_mode_missing_source_dir_rejected(self) -> None:
        with pytest.raises(ValidationError) as info:
            MemoryRunPreflightArgs()
        assert "source_dir is required" in str(info.value)

    def test_unknown_data_source_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MemoryRunPreflightArgs(data_source="history")  # type: ignore[arg-type]


class TestMemoryStartFineTuneArgs:
    """Privileged start args (admin guardrails + plan fields)."""

    def test_trajectory_mode_with_guardrails(self) -> None:
        args = MemoryStartFineTuneArgs(
            data_source="trajectory",
            confirm=True,
            reason="adopt learned trajectories",
        )
        assert args.data_source == "trajectory"
        assert args.source_dir is None

    def test_directory_mode_requires_source_dir(self) -> None:
        with pytest.raises(ValidationError) as info:
            MemoryStartFineTuneArgs(
                data_source="directory",
                confirm=True,
                reason="train on corpus",
            )
        assert "source_dir is required" in str(info.value)
