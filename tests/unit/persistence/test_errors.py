"""Tests for persistence error hierarchy."""

import pytest

from synthorg.persistence.errors import (
    DuplicateRecordError,
    MigrationError,
    PersistenceConnectionError,
    PersistenceError,
    QueryError,
    RecordNotFoundError,
)

_SUBCLASSES = [
    PersistenceConnectionError,
    MigrationError,
    RecordNotFoundError,
    DuplicateRecordError,
    QueryError,
]


@pytest.mark.unit
class TestPersistenceErrorHierarchy:
    def test_base_is_exception(self) -> None:
        assert issubclass(PersistenceError, Exception)

    @pytest.mark.parametrize("cls", _SUBCLASSES)
    def test_subclass_inherits_from_base(
        self,
        cls: type[PersistenceError],
    ) -> None:
        """All error subclasses inherit from PersistenceError."""
        assert issubclass(cls, PersistenceError)

    @pytest.mark.parametrize("cls", _SUBCLASSES)
    def test_catch_all_with_base(
        self,
        cls: type[PersistenceError],
    ) -> None:
        """All subclasses are caught by except PersistenceError."""
        msg = "test"
        with pytest.raises(PersistenceError):
            raise cls(msg)

    def test_error_message_preserved(self) -> None:
        err = PersistenceConnectionError("db down")
        assert str(err) == "db down"

    def test_does_not_shadow_builtin(self) -> None:
        """Our error is NOT the builtin ConnectionError."""
        assert PersistenceConnectionError is not ConnectionError  # type: ignore[comparison-overlap]
