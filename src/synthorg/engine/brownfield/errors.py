"""Brownfield codebase intake error hierarchy.

All conditions descend from :class:`BrownfieldError` (an engine-layer
:class:`~synthorg.engine.errors.EngineError`) so the RFC 9457 prefix-vs-category
validator runs on every subclass.
"""

from typing import ClassVar

from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.errors import EngineError


class BrownfieldError(EngineError):
    """Base exception for brownfield codebase intake failures."""

    default_message: ClassVar[str] = "Brownfield codebase intake failed"


class BrownfieldWorkspaceNotEmptyError(BrownfieldError):
    """Raised when import targets a workspace that already holds a codebase.

    Importing onto an existing codebase is destructive; the operator must
    use an explicit reset operation instead. Re-importing the *same* source
    (matching ``source_ref`` and scan content hash) is idempotent and does
    not raise.
    """

    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_CONFLICT
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    default_message: ClassVar[str] = (
        "Project workspace already holds a different codebase"
    )

    def __init__(self, *, project_id: NotBlankStr) -> None:
        super().__init__(self.default_message)
        self.project_id: NotBlankStr = project_id


class BrownfieldSourceUnavailableError(BrownfieldError):
    """Raised when the import source cannot be cloned or read.

    Covers an invalid/disallowed source reference and an unreachable remote.
    A validation-class failure: the operator must correct the source, not
    retry as-is.
    """

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Import source is unavailable"


class BrownfieldScanError(BrownfieldError):
    """Raised when the structure-map scan fails over the imported tree."""

    default_message: ClassVar[str] = "Codebase structure scan failed"
