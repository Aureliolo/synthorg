"""Tests for the per-domain ``NotFoundError`` subclasses.

Pins:

- Each subclass carries the documented ``error_code`` ClassVar.
- Each is a true :class:`NotFoundError` (so the API exception
  handler routes it through ``handle_domain_error`` without an
  explicit ``NotFoundError``-keyed entry).
- The constructor accepts a single positional message and renders
  the canonical ``"<resource_type> <identifier!r> not found"``
  string used across controllers.
"""

import pytest

from synthorg.core.domain_errors import (
    AbTestNotFoundError,
    DomainError,
    MemoryEntryNotFoundError,
    NotFoundError,
    ResourceNotFoundError,
)
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.engine.errors import TaskNotFoundError, WorkflowExecutionNotFoundError
from synthorg.engine.workflow.service import WorkflowDefinitionNotFoundError
from synthorg.integrations.errors import (
    ConnectionNotFoundError,
    SecretRetrievalNotFoundError,
)

pytestmark = pytest.mark.unit


# Subclasses defined in core/domain_errors.py.  Each carries a distinct
# 3xxx ``error_code`` and inherits :class:`NotFoundError` so the API
# exception handler routes via ``handle_domain_error`` automatically.
_CORE_SUBCLASS_CODES: tuple[tuple[type[NotFoundError], ErrorCode], ...] = (
    (ResourceNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (MemoryEntryNotFoundError, ErrorCode.MEMORY_ENTRY_NOT_FOUND),
    (AbTestNotFoundError, ErrorCode.AB_TEST_NOT_FOUND),
)

# Subclasses defined OUTSIDE core/ that multi-inherit
# :class:`NotFoundError` for API-helper compatibility.  Listed here so
# the inheritance contract surfaces in one place; if any of them stops
# being a ``NotFoundError`` the API helper ``require_resource_or_404``
# would silently lose its 404 semantic.
_FOREIGN_SUBCLASS_CODES: tuple[tuple[type[NotFoundError], ErrorCode], ...] = (
    (TaskNotFoundError, ErrorCode.TASK_NOT_FOUND),
    (WorkflowExecutionNotFoundError, ErrorCode.WORKFLOW_EXECUTION_NOT_FOUND),
    (WorkflowDefinitionNotFoundError, ErrorCode.WORKFLOW_DEFINITION_NOT_FOUND),
    (ConnectionNotFoundError, ErrorCode.CONNECTION_NOT_FOUND),
    # Deliberate uniform-404 override for reveal_secret: carries the same
    # generic RESOURCE_NOT_FOUND as a plain NotFoundError so a secret-backend
    # failure is indistinguishable from a missing connection.
    (SecretRetrievalNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
)


class TestCoreNotFoundSubclasses:
    """Subclasses declared in ``synthorg.core.domain_errors``."""

    @pytest.mark.parametrize(("cls", "expected_code"), _CORE_SUBCLASS_CODES)
    def test_carries_expected_error_code(
        self,
        cls: type[NotFoundError],
        expected_code: ErrorCode,
    ) -> None:
        """The subclass's ``error_code`` ClassVar matches the taxonomy."""
        assert cls.error_code == expected_code

    @pytest.mark.parametrize(("cls", "_"), _CORE_SUBCLASS_CODES)
    def test_inherits_not_found_status_and_category(
        self,
        cls: type[NotFoundError],
        _: ErrorCode,
    ) -> None:
        """Status code stays 404 and category stays NOT_FOUND."""
        assert cls.status_code == 404
        assert cls.error_category == ErrorCategory.NOT_FOUND

    @pytest.mark.parametrize(("cls", "_"), _CORE_SUBCLASS_CODES)
    def test_is_not_found_error_subclass(
        self,
        cls: type[NotFoundError],
        _: ErrorCode,
    ) -> None:
        """The subclass is a NotFoundError so handle_domain_error routes it."""
        assert issubclass(cls, NotFoundError)
        assert issubclass(cls, DomainError)

    @pytest.mark.parametrize(("cls", "_"), _CORE_SUBCLASS_CODES)
    def test_constructor_accepts_message(
        self,
        cls: type[NotFoundError],
        _: ErrorCode,
    ) -> None:
        """Single positional message argument renders verbatim."""
        message = "task 'abc-123' not found"
        exc = cls(message)
        assert str(exc) == message

    @pytest.mark.parametrize(("cls", "expected_code"), _CORE_SUBCLASS_CODES)
    def test_instance_error_code_matches_classvar(
        self,
        cls: type[NotFoundError],
        expected_code: ErrorCode,
    ) -> None:
        """Instances expose ``error_code`` from the ClassVar without mutation."""
        exc = cls("any message")
        assert exc.error_code == expected_code


class TestForeignNotFoundSubclasses:
    """NotFoundError subclasses living outside ``core/domain_errors``."""

    @pytest.mark.parametrize(("cls", "expected_code"), _FOREIGN_SUBCLASS_CODES)
    def test_carries_expected_error_code(
        self,
        cls: type[NotFoundError],
        expected_code: ErrorCode,
    ) -> None:
        """Foreign subclass error_code matches the taxonomy."""
        assert cls.error_code == expected_code

    @pytest.mark.parametrize(("cls", "_"), _FOREIGN_SUBCLASS_CODES)
    def test_is_not_found_error_subclass(
        self,
        cls: type[NotFoundError],
        _: ErrorCode,
    ) -> None:
        """Multi-inheritance preserves NotFoundError membership.

        :func:`synthorg.api.responses.require_resource_or_404` accepts
        ``type[NotFoundError]``; losing this inheritance would force
        every controller using the helper through a typed alternative.
        """
        assert issubclass(cls, NotFoundError)

    @pytest.mark.parametrize(("cls", "_"), _FOREIGN_SUBCLASS_CODES)
    def test_status_code_is_404(
        self,
        cls: type[NotFoundError],
        _: ErrorCode,
    ) -> None:
        """Foreign subclasses preserve the 404 status mapping."""
        assert cls.status_code == 404
