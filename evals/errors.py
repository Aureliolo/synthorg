"""Domain error hierarchy for the eval spine.

The eval spine is out-of-package, but it follows the project convention
of subclassing :class:`synthorg.core.domain_errors.DomainError` instead
of bare ``Exception`` so that error names carry intent and operators
can grep for the same taxonomy here as inside the package.

Eval errors are surfaced to the operator running the benchmark, not to
an HTTP / RPC client, so all eval errors fall under
:class:`ErrorCategory.INTERNAL` with :class:`ErrorCode.INTERNAL_ERROR`.
The category exists to satisfy the ``__init_subclass__`` invariant in
:class:`DomainError`; the eval CLI does not consult the HTTP status
code field.
"""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class EvalError(DomainError):
    """Base for every error raised by the golden-company benchmark."""

    default_message: ClassVar[str] = "Eval benchmark failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500


class BriefSuiteEmptyError(EvalError):
    """Raised when the briefs directory contains no brief files."""

    default_message: ClassVar[str] = "No brief YAML files found in the suite directory"


class BriefSuiteDuplicateIdError(EvalError):
    """Raised when two brief files declare the same ``brief_id``."""

    default_message: ClassVar[str] = "Duplicate brief_id across the suite"


class BriefSuitePathTraversalError(EvalError):
    """Raised when a globbed brief file escapes the briefs directory.

    Glob results are trusted by suffix alone, so a ``*.yaml`` symlink
    or a directory entry with a YAML suffix could otherwise read a
    file outside the briefs directory. The loader resolves each path
    and refuses anything that is not a regular file inside the
    briefs directory's resolved root.
    """

    default_message: ClassVar[str] = "Brief file escapes the briefs directory"


class BriefShapeError(EvalError):
    """Raised when a brief's ``kind`` does not match its payload (checks vs rubric)."""

    default_message: ClassVar[str] = "Brief shape does not match its declared kind"


class CassettePlaybackUnavailableError(EvalError):
    """Raised when the synthorg cassette playback hook is not installable.

    The eval refuses to fall back to live LLM calls; a missing playback
    seam is a sharp failure, not a soft degrade.
    """

    default_message: ClassVar[str] = "Cassette playback hook is not available"


class CassetteNotFoundError(EvalError):
    """Raised when ``--cassette`` points at a path that does not exist."""

    default_message: ClassVar[str] = "Cassette file not found"


class BriefTimeoutError(EvalError):
    """Raised when a brief exceeds its wall-clock safety budget."""

    default_message: ClassVar[str] = "Brief exceeded its wall-clock safety budget"


class JudgeCalibrationFailedError(EvalError):
    """Raised when the judge fails the ordinal-correlation gate against its anchors."""

    default_message: ClassVar[str] = (
        "Judge ordering does not correlate with the hand-scored anchor set"
    )


class JudgeAnchorSetTooSmallError(EvalError):
    """Raised when an anchor set has fewer items than the calibration minimum."""

    default_message: ClassVar[str] = "Judge anchor set is below the calibration minimum"


class EvalToolMissingError(EvalError):
    """Raised when a brief's hidden-test or build command cannot be launched."""

    default_message: ClassVar[str] = "Required eval tool is not on PATH"


class CompanyConfigInvalidError(EvalError):
    """Raised when a company config YAML cannot be parsed into a CompanyConfig."""

    default_message: ClassVar[str] = "Company config YAML failed validation"


class BriefExecutionError(EvalError):
    """Raised when the runner cannot boot a company or run a brief to a result."""

    default_message: ClassVar[str] = "Brief execution failed to produce a result"


class ResearchBriefUnsupportedError(EvalError):
    """Raised when a research brief is run without a research-mode integration.

    Research briefs grade a :class:`~synthorg.research.models.ResearchRun`, which
    the agent-execution runner does not yet produce. The eval refuses to score a
    research brief it cannot honestly run rather than emitting a fabricated zero.
    """

    default_message: ClassVar[str] = (
        "Research briefs require a research-mode runner integration"
    )


__all__ = [
    "BriefExecutionError",
    "BriefShapeError",
    "BriefSuiteDuplicateIdError",
    "BriefSuiteEmptyError",
    "BriefSuitePathTraversalError",
    "BriefTimeoutError",
    "CassetteNotFoundError",
    "CassettePlaybackUnavailableError",
    "CompanyConfigInvalidError",
    "EvalError",
    "EvalToolMissingError",
    "JudgeAnchorSetTooSmallError",
    "JudgeCalibrationFailedError",
    "ResearchBriefUnsupportedError",
]
