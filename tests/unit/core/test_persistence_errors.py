"""Tests for ``synthorg.core.persistence_errors``."""

import pytest

from synthorg.core.persistence_errors import (
    ArtifactStorageFullError,
    ArtifactTooLargeError,
    ConstraintViolationError,
    DuplicateRecordError,
    MalformedRowError,
    MigrationError,
    PersistenceConnectionError,
    PersistenceError,
    PersistenceVersionConflictError,
    QueryError,
    RecordNotFoundError,
)

pytestmark = pytest.mark.unit


class TestHierarchy:
    """Every concrete persistence error inherits from ``PersistenceError``."""

    def test_concrete_classes_inherit_persistence_error(self) -> None:
        for cls in (
            PersistenceConnectionError,
            MigrationError,
            RecordNotFoundError,
            DuplicateRecordError,
            QueryError,
            ConstraintViolationError,
            PersistenceVersionConflictError,
            MalformedRowError,
            ArtifactTooLargeError,
            ArtifactStorageFullError,
        ):
            assert issubclass(cls, PersistenceError), cls

    def test_constraint_violation_is_query_error(self) -> None:
        assert issubclass(ConstraintViolationError, QueryError)

    def test_version_conflict_is_query_error(self) -> None:
        assert issubclass(PersistenceVersionConflictError, QueryError)

    def test_malformed_row_is_query_error(self) -> None:
        assert issubclass(MalformedRowError, QueryError)


class TestRetryabilityFlags:
    """``is_retryable`` reflects whether a bare retry can recover."""

    @pytest.mark.parametrize(
        ("cls", "expected"),
        [
            (PersistenceError, False),
            (PersistenceConnectionError, True),
            (MigrationError, False),
            (RecordNotFoundError, False),
            (DuplicateRecordError, False),
            (QueryError, True),
            (PersistenceVersionConflictError, False),
            (MalformedRowError, False),
            (ArtifactTooLargeError, False),
            (ArtifactStorageFullError, False),
        ],
    )
    def test_classvar(self, cls: type[PersistenceError], expected: bool) -> None:
        assert cls.is_retryable is expected


class TestConstraintViolationConstructor:
    """``ConstraintViolationError`` carries a ``constraint`` attribute."""

    def test_requires_constraint_kwarg(self) -> None:
        with pytest.raises(TypeError):
            ConstraintViolationError("violated")  # type: ignore[call-arg]

    def test_records_constraint(self) -> None:
        exc = ConstraintViolationError("violated", constraint="users_email_uniq")
        assert exc.constraint == "users_email_uniq"
        assert str(exc) == "violated"

    def test_strips_whitespace_from_constraint(self) -> None:
        exc = ConstraintViolationError("violated", constraint="  users_email_uniq  ")
        assert exc.constraint == "users_email_uniq"

    @pytest.mark.parametrize("blank", ["", "   ", "\t", "\n"])
    def test_blank_constraint_normalises_to_sentinel(self, blank: str) -> None:
        """Blank input maps to a sentinel instead of raising.

        Raising :class:`ValueError` from ``__init__`` would bypass
        downstream ``except PersistenceError`` handlers, so blank
        constraints normalise to ``ConstraintViolationError.UNKNOWN_CONSTRAINT``
        instead.  Callers branching on the constraint token can detect
        the sentinel explicitly.
        """
        exc = ConstraintViolationError("violated", constraint=blank)
        assert exc.constraint == ConstraintViolationError.UNKNOWN_CONSTRAINT
        assert isinstance(exc, ConstraintViolationError)

    def test_constraint_violation_overrides_query_retry(self) -> None:
        """``ConstraintViolationError`` is non-retryable despite ``QueryError``."""
        assert ConstraintViolationError.is_retryable is False
