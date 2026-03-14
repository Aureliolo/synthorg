"""Tests for observability-specific enumerations."""

import pytest

from synthorg.observability.enums import LogLevel, RotationStrategy, SinkType

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestLogLevel:
    """Tests for LogLevel enum."""

    def test_all_members_exist(self) -> None:
        members = set(LogLevel)
        assert len(members) == 5
        assert LogLevel.DEBUG in members
        assert LogLevel.INFO in members
        assert LogLevel.WARNING in members
        assert LogLevel.ERROR in members
        assert LogLevel.CRITICAL in members

    def test_values_are_strings(self) -> None:
        assert LogLevel.DEBUG.value == "DEBUG"
        assert LogLevel.INFO.value == "INFO"
        assert LogLevel.WARNING.value == "WARNING"
        assert LogLevel.ERROR.value == "ERROR"
        assert LogLevel.CRITICAL.value == "CRITICAL"

    def test_membership(self) -> None:
        assert "DEBUG" in LogLevel.__members__.values()
        assert "INFO" in LogLevel.__members__.values()

    def test_is_str_subclass(self) -> None:
        assert isinstance(LogLevel.DEBUG, str)


@pytest.mark.unit
class TestRotationStrategy:
    """Tests for RotationStrategy enum."""

    def test_all_members_exist(self) -> None:
        members = set(RotationStrategy)
        assert len(members) == 2
        assert RotationStrategy.BUILTIN in members
        assert RotationStrategy.EXTERNAL in members

    def test_values_are_strings(self) -> None:
        assert RotationStrategy.BUILTIN.value == "builtin"
        assert RotationStrategy.EXTERNAL.value == "external"

    def test_is_str_subclass(self) -> None:
        assert isinstance(RotationStrategy.BUILTIN, str)


@pytest.mark.unit
class TestSinkType:
    """Tests for SinkType enum."""

    def test_all_members_exist(self) -> None:
        members = set(SinkType)
        assert len(members) == 2
        assert SinkType.CONSOLE in members
        assert SinkType.FILE in members

    def test_values_are_strings(self) -> None:
        assert SinkType.CONSOLE.value == "console"
        assert SinkType.FILE.value == "file"

    def test_is_str_subclass(self) -> None:
        assert isinstance(SinkType.CONSOLE, str)
