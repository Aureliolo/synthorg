"""Tests for config error types and formatting."""

import pytest

from synthorg.config.errors import (
    ConfigError,
    ConfigFileNotFoundError,
    ConfigLocation,
    ConfigParseError,
    ConfigValidationError,
)


@pytest.mark.unit
class TestConfigLocation:
    def test_creation(self) -> None:
        loc = ConfigLocation(
            file_path="config.yaml",
            key_path="budget.alerts",
            line=12,
            column=3,
        )
        assert loc.file_path == "config.yaml"
        assert loc.key_path == "budget.alerts"
        assert loc.line == 12
        assert loc.column == 3

    def test_defaults(self) -> None:
        loc = ConfigLocation()
        assert loc.file_path is None
        assert loc.key_path is None
        assert loc.line is None
        assert loc.column is None

    def test_frozen(self) -> None:
        loc = ConfigLocation(file_path="config.yaml")
        with pytest.raises(AttributeError):
            loc.file_path = "other.yaml"  # type: ignore[misc]


@pytest.mark.unit
class TestConfigError:
    def test_str_without_locations(self) -> None:
        err = ConfigError("Something failed")
        assert str(err) == "Something failed"

    def test_str_with_locations(self) -> None:
        err = ConfigError(
            "Something failed",
            locations=(
                ConfigLocation(
                    file_path="config.yaml",
                    key_path="budget",
                    line=5,
                    column=3,
                ),
            ),
        )
        result = str(err)
        assert "Something failed" in result
        assert "budget" in result
        assert "config.yaml" in result
        assert "line 5, column 3" in result

    def test_str_with_file_only_location(self) -> None:
        err = ConfigError(
            "Parse failed",
            locations=(ConfigLocation(file_path="config.yaml", line=3),),
        )
        result = str(err)
        assert "config.yaml" in result
        assert "line 3" in result
        assert "column" not in result

    def test_str_with_file_path_no_key_path(self) -> None:
        err = ConfigError(
            "Parse failed",
            locations=(ConfigLocation(file_path="config.yaml"),),
        )
        result = str(err)
        assert "config.yaml" in result

    def test_inherits_exception(self) -> None:
        assert isinstance(ConfigError("test"), Exception)

    def test_message_attribute(self) -> None:
        err = ConfigError("hello")
        assert err.message == "hello"
        assert err.locations == ()


@pytest.mark.unit
class TestConfigFileNotFoundError:
    def test_inherits_config_error(self) -> None:
        assert isinstance(ConfigFileNotFoundError("not found"), ConfigError)

    def test_message(self) -> None:
        err = ConfigFileNotFoundError("File missing: config.yaml")
        assert str(err) == "File missing: config.yaml"


@pytest.mark.unit
class TestConfigParseError:
    def test_inherits_config_error(self) -> None:
        assert isinstance(ConfigParseError("bad yaml"), ConfigError)

    def test_message(self) -> None:
        err = ConfigParseError("YAML syntax error")
        assert "YAML syntax error" in str(err)


@pytest.mark.unit
class TestConfigValidationError:
    def test_inherits_config_error(self) -> None:
        assert isinstance(
            ConfigValidationError("validation failed"),
            ConfigError,
        )

    def test_per_field_errors_formatting(self) -> None:
        err = ConfigValidationError(
            "Configuration validation failed",
            locations=(
                ConfigLocation(
                    file_path="config.yaml",
                    key_path="budget.alerts.warn_at",
                    line=12,
                    column=5,
                ),
                ConfigLocation(
                    file_path="config.yaml",
                    key_path="agents.0.name",
                    line=25,
                ),
            ),
            field_errors=(
                ("budget.alerts.warn_at", "Input should be <= 100"),
                ("agents.0.name", "String should have at least 1 character"),
            ),
        )
        result = str(err)
        assert "2 errors" in result
        assert "budget.alerts.warn_at: Input should be <= 100" in result
        assert "agents.0.name: String should have at least 1 character" in result
        assert "config.yaml" in result
        assert "line 12, column 5" in result
        assert "line 25" in result

    def test_field_error_without_matching_location(self) -> None:
        err = ConfigValidationError(
            "validation failed",
            locations=(),
            field_errors=(("budget.total_monthly", "must be positive"),),
        )
        result = str(err)
        assert "budget.total_monthly: must be positive" in result
        assert "in " not in result

    def test_no_field_errors_falls_back(self) -> None:
        err = ConfigValidationError("validation failed")
        assert str(err) == "validation failed"

    def test_field_errors_attribute(self) -> None:
        err = ConfigValidationError(
            "bad",
            field_errors=(("x", "wrong"),),
        )
        assert err.field_errors == (("x", "wrong"),)
