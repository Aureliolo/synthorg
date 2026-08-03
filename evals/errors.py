# module-kind: code
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


class WorkspaceSpecMissingError(EvalError):
    """Raised when a workspace operation is asked for a brief with no workspace."""

    default_message: ClassVar[str] = "Brief does not declare a workspace block"


class WorkspaceSeedNotFoundError(EvalError):
    """Raised when a brief's seed fixture directory does not exist.

    Seeding an empty workspace would silently hand every loop a blank slate
    and grade them all at zero, which reads as a measured result rather than
    a broken harness. Fail closed instead.
    """

    default_message: ClassVar[str] = "Brief workspace seed fixture not found"


class WorkspacePathEscapeError(EvalError):
    """Raised when a resolved workspace path escapes its containing root.

    ``brief_id`` and ``seed_dir`` both arrive from authored YAML, so every
    path built from them is re-checked after resolution rather than trusted.
    """

    default_message: ClassVar[str] = "Workspace path escapes its root directory"


class ProvenanceUnavailableError(EvalError):
    """Raised when a scoreboard's commit provenance cannot be read.

    A scoreboard that cannot name the commit it measured is not reproducible,
    and reproducibility is an acceptance criterion for the A/B rather than a
    nicety, so this fails closed instead of stamping an unknown placeholder.
    """

    default_message: ClassVar[str] = "Scoreboard git provenance is unavailable"


class LoopAbProviderMissingError(EvalError):
    """Raised when a loop A/B manifest tier names an unknown provider.

    The manifest binds each tier to an explicit ``(provider, model)`` pair, so a
    tier naming a provider absent from the company config is a configuration
    error that must fail loud before any real-spend run, not a bare ``KeyError``
    that loses the domain taxonomy and its structured context.
    """

    default_message: ClassVar[str] = (
        "Loop A/B manifest tier names a provider absent from the company config"
    )


class LoopAbGatewayUnavailableError(EvalError):
    """Raised when the recorder's hosted gateway did not come up wired.

    The recording host exists so mint and verify are the same
    :class:`~synthorg.llm.gateway_token.GatewaySigner` instance. A host that
    booted without one cannot authenticate a single cell, so it fails here
    rather than recording every row as unavailable for a reason nobody can act
    on.
    """

    default_message: ClassVar[str] = (
        "Loop A/B recording host has no gateway signer to mint run bearers with"
    )


class LoopAbOpenHandsUnwiredError(EvalError):
    """Raised when the OpenHands loop's runtime is not wired for a cell.

    The boundary reports the missing piece at WARNING as it declines to wire, so
    this carries the cell into the runner's unavailable row rather than letting
    a ``None`` reach the loop factory and fail with a less specific message.
    """

    default_message: ClassVar[str] = (
        "OpenHands loop runtime is unwired; see the logged missing pieces"
    )


class LoopAbBindHostUnresolvedError(EvalError):
    """Raised when the interface the recording host should listen on is unknown.

    The container dials the recorder through a ``host-gateway`` alias, so the
    listener has to sit on an address that alias resolves to. Binding every
    interface would always satisfy that, and is exactly what this refuses to do
    silently: the host serves the whole application, including the fail-safe
    excluded ``/auth/setup``, so a wide bind is the operator's explicit call to
    make via ``--bind-host``, never a default the harness picks for them.
    """

    default_message: ClassVar[str] = (
        "Could not resolve an interface reachable from the sandbox; "
        "pass --bind-host explicitly"
    )


class LoopAbHostAlreadyStartedError(EvalError):
    """Raised when a started recording host is started a second time.

    The second start would capture the first start's throwaway bootstrap
    secrets as the values to restore, so stopping would leave the operator's
    real environment holding secrets that died with a process.
    """

    default_message: ClassVar[str] = "Loop A/B recording host is already started"


class LoopAbDockerUnavailableError(EvalError):
    """Raised when the Docker daemon is unreachable before a recording run.

    Every loop drives a sandbox, so a run without a daemon measures nothing.
    Discovering that inside a cell would first spend real provider tokens and
    then record the failure as that loop's unavailable row, which reads as a
    property of the loop rather than of the machine.
    """

    default_message: ClassVar[str] = "Docker daemon is unreachable"


class LoopAbNoCellsMeasuredError(EvalError):
    """Raised when a completed matrix scored no cell at all.

    An all-unavailable scoreboard is never a legitimate measurement, and
    writing one exits successfully with a file that looks like a result. The
    usual cause is a company config whose ``providers`` block does not cover
    the manifest's tiers.
    """

    default_message: ClassVar[str] = "Loop A/B matrix measured no cells"


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
    "LoopAbBindHostUnresolvedError",
    "LoopAbDockerUnavailableError",
    "LoopAbGatewayUnavailableError",
    "LoopAbHostAlreadyStartedError",
    "LoopAbNoCellsMeasuredError",
    "LoopAbOpenHandsUnwiredError",
    "LoopAbProviderMissingError",
    "ProvenanceUnavailableError",
    "ResearchBriefUnsupportedError",
    "WorkspacePathEscapeError",
    "WorkspaceSeedNotFoundError",
    "WorkspaceSpecMissingError",
]
