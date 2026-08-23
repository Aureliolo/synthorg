"""Unit tests for the sprint workflow configuration model."""

import pytest

from synthorg.engine.workflow.sprint_config import SprintConfig


class TestSprintConfig:
    """SprintConfig validates sprint workflow settings."""

    @pytest.mark.unit
    def test_default_config(self) -> None:
        config = SprintConfig()
        assert config.duration_days == 14
        assert config.max_tasks_per_sprint == 50
        assert config.velocity_window == 3

    @pytest.mark.unit
    def test_custom_config(self) -> None:
        config = SprintConfig(
            duration_days=7,
            max_tasks_per_sprint=20,
            velocity_window=5,
        )
        assert config.duration_days == 7
        assert config.max_tasks_per_sprint == 20
        assert config.velocity_window == 5

    @pytest.mark.unit
    def test_duration_bounds(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal"):
            SprintConfig(duration_days=0)
        with pytest.raises(ValueError, match="less than or equal"):
            SprintConfig(duration_days=91)

    @pytest.mark.unit
    def test_velocity_window_bounds(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal"):
            SprintConfig(velocity_window=0)
        with pytest.raises(ValueError, match="less than or equal"):
            SprintConfig(velocity_window=21)

    @pytest.mark.unit
    def test_max_tasks_bounds(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal"):
            SprintConfig(max_tasks_per_sprint=0)
        with pytest.raises(ValueError, match="less than or equal"):
            SprintConfig(max_tasks_per_sprint=501)

    @pytest.mark.unit
    def test_frozen(self) -> None:
        config = SprintConfig()
        with pytest.raises(ValueError, match="frozen"):
            config.duration_days = 7  # type: ignore[misc]
