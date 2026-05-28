"""Adversarial red-team error hierarchy.

All red-team errors descend from :class:`RedTeamError`, which is an
:class:`EngineError` so the prefix-vs-category validator from
:class:`synthorg.core.domain_errors.DomainError` runs at class definition.
"""

from typing import ClassVar

from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import EngineError


class RedTeamError(EngineError):
    """Base for red-team gate errors.

    Subclasses set narrower ``status_code`` / ``error_code`` /
    ``default_message`` so HTTP exposure and structured logs stay
    domain-meaningful. The base inherits the 5xx INTERNAL defaults
    from :class:`EngineError`.
    """


class RedTeamReportNotFoundError(RedTeamError):
    """Raised when the gate expects a report for an execution_id and finds none.

    Triggered after the agent run returns without having called
    ``submit_red_team_report``. The gate's failure policy is fail-OPEN
    with a synthetic informational finding (see :class:`RedTeamGateService`),
    but the raw error is still emitted so downstream tooling can detect
    a broken agent before the gate silently lets defects through.
    """

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.ENGINE_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = (
        "Red-team agent did not produce a report for the deliverable"
    )

    def __init__(self, *, execution_id: NotBlankStr) -> None:
        super().__init__(self.default_message)
        self.execution_id: NotBlankStr = execution_id


class RedTeamReportValidationError(RedTeamError):
    """Raised when a ``submit_red_team_report`` payload fails validation.

    Maps to 422 because the agent's tool-call argument is the request
    payload and Pydantic surfaced a structural mismatch (unknown field,
    severity out of range, evidence missing on HIGH findings, etc.).
    """

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.REQUEST_VALIDATION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = (
        "Red-team report payload failed structural validation"
    )


class RedTeamDispatchError(RedTeamError):
    """Raised when the gate cannot dispatch the red-team agent.

    Distinct from :class:`RedTeamReportNotFoundError`: the agent never
    ran (provider missing, transient-task construction failed, agent
    identity not registered). Maps to 500 because this is an
    operator-fixable wiring fault, not a request-payload issue.
    """

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.ENGINE_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = (
        "Red-team agent dispatch failed before the agent could file a report"
    )


class RedTeamReportAlreadyExistsError(RedTeamError):
    """Raised when a second report is submitted for the same execution_id.

    The ``submit_red_team_report`` tool is single-shot per execution; a
    second call is either a duplicate or an adversarial attempt to
    overwrite the first report. Maps to 409 (Conflict).
    """

    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_CONFLICT
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    default_message: ClassVar[str] = (
        "A red-team report already exists for this execution"
    )

    def __init__(self, *, execution_id: NotBlankStr) -> None:
        super().__init__(self.default_message)
        self.execution_id: NotBlankStr = execution_id


class RedTeamRoleMissingError(RedTeamError):
    """Raised when the built-in ``Red Team`` role is absent from the catalog.

    Surfaces as a hard configuration error (not a silent fallback)
    because the agent identity factory cannot construct a meaningful
    :class:`AgentIdentity` without the catalogued role.
    """

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.ENGINE_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = (
        "Built-in red-team role missing from BUILTIN_ROLES catalog"
    )


class RedTeamRuntimeSeedIncompleteError(RedTeamError):
    """Raised when the runtime builder is invoked without a complete seed.

    ``build_red_team_runtime`` requires the report repository and the
    submit-report tool to be pre-built so the tool is registered on the
    agent engine's shared tool registry. A ``None`` here is a wiring
    fault: the builder caller did not run ``build_red_team_tool_seed``
    before the engine was assembled.
    """

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.ENGINE_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = (
        "Red-team runtime seed is incomplete (report_repo / submit_tool unset)"
    )
