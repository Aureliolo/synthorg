"""RFC 9457 error taxonomy (categories + codes).

This module is a pure leaf: it owns the canonical
``ErrorCategory`` / ``ErrorCode`` enums plus the small helper functions
that derive RFC 9457 ``title`` and ``type`` URIs from a category.  Every
domain-error and HTTP-error class in the project reads its metadata from
here, which is why the module is dependency-free apart from stdlib --
keeping the boundary intact lets the CLI and any future extension import
error metadata without pulling in ``synthorg.api`` or
``synthorg.persistence``.

Public constants (``NOT_FOUND_BAND``, ``CODE_CATEGORY_PREFIX``,
``CATEGORY_TITLES``) are exported without an underscore prefix because
they are imported across the package -- by ``synthorg.core.domain_errors``
to validate ``error_code`` / ``error_category`` consistency in
``DomainError.__init_subclass__``, and by ``tests/unit/core/`` and
``tests/unit/architecture/`` to assert layering invariants.  An
underscore prefix would have implied module-private intent that does
not match how they are consumed.
"""

from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import Final


class ErrorCategory(StrEnum):
    """High-level error category for structured error responses.

    Values are lowercase strings suitable for JSON serialization.
    """

    AUTH = "auth"
    VALIDATION = "validation"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMIT = "rate_limit"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PROVIDER_ERROR = "provider_error"
    INTERNAL = "internal"


class ErrorCode(IntEnum):
    """Machine-readable error codes (4-digit, category-grouped).

    First digit encodes the category:
    1xxx = auth, 2xxx = validation, 3xxx = not_found, 4xxx = conflict,
    5xxx = rate_limit, 6xxx = budget_exhausted, 7xxx = provider_error,
    8xxx = internal.
    """

    # 1xxx -- auth
    UNAUTHORIZED = 1000
    FORBIDDEN = 1001
    SESSION_REVOKED = 1002
    ACCOUNT_LOCKED = 1003
    CSRF_REJECTED = 1004
    REFRESH_TOKEN_INVALID = 1005
    SESSION_LIMIT_EXCEEDED = 1006
    TOOL_PERMISSION_DENIED = 1007

    # 2xxx -- validation
    VALIDATION_ERROR = 2000
    REQUEST_VALIDATION_ERROR = 2001
    ARTIFACT_TOO_LARGE = 2002
    TOOL_PARAMETER_ERROR = 2003
    PROVIDER_TIER_COVERAGE_INSUFFICIENT = 2004

    # 3xxx -- not_found
    RESOURCE_NOT_FOUND = 3000
    RECORD_NOT_FOUND = 3001
    ROUTE_NOT_FOUND = 3002
    PROJECT_NOT_FOUND = 3003
    TASK_NOT_FOUND = 3004
    SUBWORKFLOW_NOT_FOUND = 3005
    WORKFLOW_EXECUTION_NOT_FOUND = 3006
    CHANNEL_NOT_FOUND = 3007
    TOOL_NOT_FOUND = 3008
    ONTOLOGY_NOT_FOUND = 3009
    CONNECTION_NOT_FOUND = 3010
    MODEL_NOT_FOUND = 3011
    ESCALATION_NOT_FOUND = 3012

    # 4xxx -- conflict
    RESOURCE_CONFLICT = 4000
    DUPLICATE_RECORD = 4001
    VERSION_CONFLICT = 4002
    TASK_VERSION_CONFLICT = 4003
    ONTOLOGY_DUPLICATE = 4004
    CHANNEL_ALREADY_EXISTS = 4005
    ESCALATION_ALREADY_DECIDED = 4006
    MIXED_CURRENCY_AGGREGATION = 4007

    # 5xxx -- rate_limit
    RATE_LIMITED = 5000
    PER_OPERATION_RATE_LIMITED = 5001
    CONCURRENCY_LIMIT_EXCEEDED = 5002

    # 6xxx -- budget_exhausted
    BUDGET_EXHAUSTED = 6000
    DAILY_LIMIT_EXCEEDED = 6001
    RISK_BUDGET_EXHAUSTED = 6002
    PROJECT_BUDGET_EXHAUSTED = 6003
    QUOTA_EXHAUSTED = 6004

    # 7xxx -- provider_error
    PROVIDER_ERROR = 7000
    PROVIDER_TIMEOUT = 7001
    PROVIDER_CONNECTION = 7002
    PROVIDER_INTERNAL = 7003
    PROVIDER_AUTHENTICATION_FAILED = 7004
    PROVIDER_INVALID_REQUEST = 7005
    PROVIDER_CONTENT_FILTERED = 7006
    INTEGRATION_ERROR = 7007
    OAUTH_ERROR = 7008
    WEBHOOK_ERROR = 7009

    # 8xxx -- internal
    INTERNAL_ERROR = 8000
    SERVICE_UNAVAILABLE = 8001
    PERSISTENCE_ERROR = 8002
    ENGINE_ERROR = 8003
    ONTOLOGY_ERROR = 8004
    COMMUNICATION_ERROR = 8005
    TOOL_ERROR = 8006
    ARTIFACT_STORAGE_FULL = 8007
    TOOL_EXECUTION_ERROR = 8008


# Error-code band for the NOT_FOUND category (3xxx).  ``resource_not_found``
# (in :mod:`synthorg.core.domain_errors`) rejects non-NOT_FOUND codes so a
# 404 response cannot accidentally carry an auth / validation / conflict
# code.
NOT_FOUND_BAND: Final[int] = 3

# Maps the first digit of an ``ErrorCode`` value to its expected category.
# ``DomainError.__init_subclass__`` (in :mod:`synthorg.core.domain_errors`)
# uses this to validate that error code prefixes match their declared
# category at class creation time.
CODE_CATEGORY_PREFIX: MappingProxyType[int, ErrorCategory] = MappingProxyType(
    {
        1: ErrorCategory.AUTH,
        2: ErrorCategory.VALIDATION,
        3: ErrorCategory.NOT_FOUND,
        4: ErrorCategory.CONFLICT,
        5: ErrorCategory.RATE_LIMIT,
        6: ErrorCategory.BUDGET_EXHAUSTED,
        7: ErrorCategory.PROVIDER_ERROR,
        8: ErrorCategory.INTERNAL,
    }
)


CATEGORY_TITLES: MappingProxyType[ErrorCategory, str] = MappingProxyType(
    {
        ErrorCategory.AUTH: "Authentication Error",
        ErrorCategory.VALIDATION: "Validation Error",
        ErrorCategory.NOT_FOUND: "Resource Not Found",
        ErrorCategory.CONFLICT: "Resource Conflict",
        ErrorCategory.RATE_LIMIT: "Rate Limit Exceeded",
        ErrorCategory.BUDGET_EXHAUSTED: "Budget Exhausted",
        ErrorCategory.PROVIDER_ERROR: "Provider Error",
        ErrorCategory.INTERNAL: "Internal Server Error",
    }
)

_ERROR_DOCS_BASE: Final[str] = "https://synthorg.io/docs/errors"


def category_title(cat: ErrorCategory) -> str:
    """Return the RFC 9457 ``title`` for a category.

    Args:
        cat: Error category.

    Returns:
        Human-readable title string.

    Raises:
        ValueError: If ``cat`` has no entry in :data:`CATEGORY_TITLES`.
            The two structures must stay in lockstep; a missing entry
            indicates :class:`ErrorCategory` was extended without
            updating :data:`CATEGORY_TITLES`, which would otherwise
            surface as an opaque ``KeyError`` deep inside the
            exception handler.
    """
    title = CATEGORY_TITLES.get(cat)
    if title is None:
        msg = (
            f"ErrorCategory {cat.name!r} has no entry in CATEGORY_TITLES; "
            "extend the mapping when adding a new category."
        )
        raise ValueError(msg)
    return title


def category_type_uri(cat: ErrorCategory) -> str:
    """Return the RFC 9457 ``type`` URI for a category.

    Args:
        cat: Error category.

    Returns:
        Documentation URI with fragment anchor for the error category.
    """
    return f"{_ERROR_DOCS_BASE}#{cat.value}"
