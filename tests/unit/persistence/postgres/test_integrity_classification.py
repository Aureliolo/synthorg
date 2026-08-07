"""Unit tests for Postgres integrity-failure classification.

Postgres names two codes for the one fact a caller acts on, that a row
still points at what they tried to remove: 23503 for a plain reference and
23001 for one declared ``ON DELETE RESTRICT``. SQLite reports both as the
same message and is mapped onto 23503, so the fold is what keeps a refused
delete reaching the API integrity handler as the same condition on either
backend. Covered here rather than only in the dual-backend conformance
harness because the mapping needs no database to exercise.
"""

import psycopg
import pytest

from synthorg.core.persistence_errors import ConstraintViolationError
from synthorg.persistence.postgres._integrity import (
    SQLSTATE_FOREIGN_KEY,
    constraint_name,
    raise_constraint_violation,
    shared_sqlstate,
)

pytestmark = pytest.mark.unit


class TestSharedSqlstate:
    def test_a_restrict_refusal_answers_with_the_foreign_key_code(self) -> None:
        """``plans.parent_task_id`` is RESTRICT, so this is the live case."""
        exc = psycopg.errors.RestrictViolation("plans still reference this task")

        assert exc.sqlstate == "23001"
        assert shared_sqlstate(exc) == SQLSTATE_FOREIGN_KEY

    def test_a_plain_reference_refusal_is_unchanged(self) -> None:
        assert (
            shared_sqlstate(psycopg.errors.ForeignKeyViolation("no parent"))
            == SQLSTATE_FOREIGN_KEY
        )

    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (psycopg.errors.UniqueViolation("dup"), "23505"),
            (psycopg.errors.NotNullViolation("null"), "23502"),
            (psycopg.errors.CheckViolation("bad"), "23514"),
        ],
    )
    def test_every_other_code_passes_through(
        self, exc: psycopg.errors.IntegrityError, expected: str
    ) -> None:
        """Only the RESTRICT code is folded; the rest are already shared."""
        assert shared_sqlstate(exc) == expected


class TestRaiseConstraintViolation:
    def test_the_typed_error_carries_the_folded_code(self) -> None:
        """The error is the contract; a raw 23001 reaches no handler branch."""
        with pytest.raises(ConstraintViolationError) as info:
            raise_constraint_violation(
                psycopg.errors.RestrictViolation("still referenced"),
                "Failed to delete task",
            )

        assert info.value.sqlstate == SQLSTATE_FOREIGN_KEY

    def test_an_unnamed_constraint_degrades_rather_than_raising(self) -> None:
        """A hand-built error has no ``diag.constraint_name`` to report."""
        assert constraint_name(psycopg.errors.RestrictViolation("x")) == "<unknown>"
