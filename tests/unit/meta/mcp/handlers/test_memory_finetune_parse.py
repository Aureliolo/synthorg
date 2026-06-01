"""Unit tests for the memory fine-tune MCP argument parser.

``parse_fine_tune_plan`` turns the loosely-typed MCP ``arguments`` dict
into a validated :class:`FineTunePlan`. These tests pin the directory /
trajectory ``data_source`` split: directory mode hard-requires
``source_dir`` (the corpus path); trajectory mode harvests the org's
working history and makes ``source_dir`` optional. A malformed
``data_source`` surfaces as an ``invalid_argument`` envelope rather than
silently falling back to directory mode.
"""

import pytest

from synthorg.memory.embedding.fine_tune_models import FineTuneDataSourceType
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handlers._memory_finetune_parse import parse_fine_tune_plan

pytestmark = pytest.mark.unit


class TestParseFineTunePlanDirectoryMode:
    """Directory mode keeps the original mandatory-``source_dir`` contract."""

    def test_directory_mode_with_source_dir(self) -> None:
        plan = parse_fine_tune_plan({"source_dir": "/data/org-docs"})
        assert plan.data_source is FineTuneDataSourceType.DIRECTORY
        assert plan.source_dir == "/data/org-docs"

    def test_explicit_directory_mode_with_source_dir(self) -> None:
        plan = parse_fine_tune_plan(
            {"data_source": "directory", "source_dir": "/data/org-docs"},
        )
        assert plan.data_source is FineTuneDataSourceType.DIRECTORY
        assert plan.source_dir == "/data/org-docs"

    def test_directory_mode_missing_source_dir_rejected(self) -> None:
        with pytest.raises(ArgumentValidationError) as info:
            parse_fine_tune_plan({"data_source": "directory"})
        assert info.value.argument == "source_dir"

    def test_implicit_directory_mode_missing_source_dir_rejected(self) -> None:
        """No ``data_source`` defaults to directory, so ``source_dir`` is required."""
        with pytest.raises(ArgumentValidationError) as info:
            parse_fine_tune_plan({})
        assert info.value.argument == "source_dir"


class TestParseFineTunePlanTrajectoryMode:
    """Trajectory mode makes ``source_dir`` optional and reaches the runner."""

    def test_trajectory_mode_without_source_dir(self) -> None:
        plan = parse_fine_tune_plan({"data_source": "trajectory"})
        assert plan.data_source is FineTuneDataSourceType.TRAJECTORY
        assert plan.source_dir is None

    def test_trajectory_mode_to_request_forwards_data_source(self) -> None:
        plan = parse_fine_tune_plan({"data_source": "trajectory"})
        request = plan.to_request()
        assert request.data_source is FineTuneDataSourceType.TRAJECTORY
        assert request.source_dir is None

    def test_trajectory_mode_keeps_present_source_dir(self) -> None:
        """A trajectory caller may still pass ``source_dir``; it is preserved."""
        plan = parse_fine_tune_plan(
            {"data_source": "trajectory", "source_dir": "/data/org-docs"},
        )
        assert plan.data_source is FineTuneDataSourceType.TRAJECTORY
        assert plan.source_dir == "/data/org-docs"

    def test_trajectory_mode_blank_source_dir_rejected(self) -> None:
        with pytest.raises(ArgumentValidationError) as info:
            parse_fine_tune_plan(
                {"data_source": "trajectory", "source_dir": "   "},
            )
        assert info.value.argument == "source_dir"


class TestParseFineTunePlanDataSourceValidation:
    """A malformed ``data_source`` is a typed error, never a silent fallback."""

    def test_unknown_data_source_rejected(self) -> None:
        with pytest.raises(ArgumentValidationError) as info:
            parse_fine_tune_plan({"data_source": "history"})
        assert info.value.argument == "data_source"

    def test_non_string_data_source_rejected(self) -> None:
        with pytest.raises(ArgumentValidationError) as info:
            parse_fine_tune_plan({"data_source": 1})
        assert info.value.argument == "data_source"
