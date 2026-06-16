"""Domain errors used by the rollback mutator subsystem."""

import pytest

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.meta.errors import (
    RollbackMutationDeniedError,
    SelfImprovementError,
    UnknownArchitectureTargetError,
)

pytestmark = pytest.mark.unit


class TestRollbackMutationDeniedError:
    """Class-level invariants used by the HTTP / MCP boundary."""

    def test_inherits_from_self_improvement_error(self) -> None:
        assert issubclass(RollbackMutationDeniedError, SelfImprovementError)

    def test_inherits_from_domain_error(self) -> None:
        """The convention-gate enforces DomainError descent."""
        assert issubclass(RollbackMutationDeniedError, DomainError)

    def test_carries_conflict_status_code(self) -> None:
        """409 mirrors the version-conflict pattern."""
        assert RollbackMutationDeniedError.status_code == 409

    def test_carries_conflict_category_and_code(self) -> None:
        assert RollbackMutationDeniedError.error_category == ErrorCategory.CONFLICT
        assert (
            RollbackMutationDeniedError.error_code == ErrorCode.ROLLBACK_MUTATION_DENIED
        )

    def test_default_message_present(self) -> None:
        err = RollbackMutationDeniedError()
        assert err.default_message
        assert "denied" in err.default_message.lower()


class TestUnknownArchitectureTargetError:
    """The router's parse-failure error inherits all the rollback metadata."""

    def test_inherits_from_rollback_mutation_denied(self) -> None:
        assert issubclass(
            UnknownArchitectureTargetError,
            RollbackMutationDeniedError,
        )

    def test_distinct_default_message(self) -> None:
        err = UnknownArchitectureTargetError()
        assert "unknown" in err.default_message.lower()

    def test_inherits_status_code(self) -> None:
        assert UnknownArchitectureTargetError.status_code == 409
