# module-kind: code
"""Completion-oracle peer-review error hierarchy.

All errors descend from :class:`CompletionOracleError`, an
:class:`EngineError`, so the prefix-vs-category validator from
:class:`synthorg.core.domain_errors.DomainError` runs at class definition.
"""

from typing import ClassVar

from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.types import NotBlankStr, require_not_blank
from synthorg.engine.errors import EngineError


class CompletionOracleError(EngineError):
    """Base for completion-oracle peer-review gate errors.

    Subclasses set narrower ``status_code`` / ``error_code`` /
    ``default_message`` so HTTP exposure and structured logs stay
    domain-meaningful. The base inherits the 5xx INTERNAL defaults from
    :class:`EngineError`.
    """


class CompletionOracleVerdictNotFoundError(CompletionOracleError):
    """Raised when the gate expects a verdict for an execution_id and finds none.

    Triggered after the reviewer run returns without having called
    ``submit_completion_oracle_verdict``. The gate's failure policy is
    fail-CLOSED (escalate to a human), but the raw error is still emitted
    so downstream tooling can detect a broken reviewer.
    """

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.COMPLETION_ORACLE_VERDICT_MISSING
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = (
        "Completion-oracle reviewer did not produce a verdict for the deliverable"
    )

    def __init__(self, *, execution_id: NotBlankStr) -> None:
        super().__init__(self.default_message)
        self.execution_id: NotBlankStr = require_not_blank(execution_id, "execution_id")


class CompletionOracleVerdictValidationError(CompletionOracleError):
    """Raised when a ``submit_completion_oracle_verdict`` payload fails validation.

    Maps to 422 because the reviewer's tool-call argument is the request
    payload and Pydantic surfaced a structural mismatch.
    """

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.COMPLETION_ORACLE_VERDICT_INVALID
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = (
        "Completion-oracle verdict payload failed structural validation"
    )


class CompletionOracleDispatchError(CompletionOracleError):
    """Raised when the gate cannot dispatch the peer-review agent.

    Distinct from :class:`CompletionOracleVerdictNotFoundError`: the agent
    never ran (provider missing, transient-task construction failed).
    """

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.COMPLETION_ORACLE_DISPATCH_FAILED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = (
        "Completion-oracle reviewer dispatch failed before a verdict was filed"
    )


class CompletionOracleVerdictAlreadyExistsError(CompletionOracleError):
    """Raised when a second verdict is submitted for the same execution_id.

    The ``submit_completion_oracle_verdict`` tool is single-shot per
    execution; a second call is a duplicate or an attempt to overwrite the
    first verdict. Maps to 409 (Conflict).
    """

    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_CONFLICT
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    default_message: ClassVar[str] = (
        "A completion-oracle verdict already exists for this execution"
    )

    def __init__(self, *, execution_id: NotBlankStr) -> None:
        super().__init__(self.default_message)
        self.execution_id: NotBlankStr = require_not_blank(execution_id, "execution_id")


class CompletionOracleRoleMissingError(CompletionOracleError):
    """Raised when the built-in ``Completion Reviewer`` role is absent.

    Surfaces as a hard configuration error (not a silent fallback) because
    the reviewer identity factory cannot construct a meaningful
    :class:`AgentIdentity` without the catalogued role.
    """

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.COMPLETION_ORACLE_ROLE_MISSING
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = (
        "Built-in completion-reviewer role missing from BUILTIN_ROLES catalog"
    )


class CompletionOracleRuntimeSeedIncompleteError(CompletionOracleError):
    """Raised when the runtime builder is invoked without a complete seed.

    ``build_completion_oracle_runtime`` requires the report repository and
    the submit tool to be pre-built so the tool is registered on the agent
    engine's shared tool registry. A ``None`` here is a wiring fault.
    """

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = (
        ErrorCode.COMPLETION_ORACLE_RUNTIME_SEED_INCOMPLETE
    )
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = (
        "Completion-oracle runtime seed is incomplete (report_repo / submit_tool unset)"
    )
