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


class HarnessProviderMissingError(EvalError):
    """Raised when a loop A/B manifest tier names an unknown provider.

    The manifest binds each tier to an explicit ``(provider, model)`` pair, so a
    tier naming a provider absent from the company config is a configuration
    error that must fail loud before any real-spend run, not a bare ``KeyError``
    that loses the domain taxonomy and its structured context.
    """

    default_message: ClassVar[str] = (
        "Loop A/B manifest tier names a provider absent from the company config"
    )


class HarnessGatewayUnavailableError(EvalError):
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


class HarnessBindHostUnresolvedError(EvalError):
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


class HarnessHostConfigInvalidError(EvalError):
    """Raised when the recording host is configured with a value it cannot bind.

    Caught alongside the other host errors, so a caller wrapping host
    construction in the eval taxonomy does not have to special-case a builtin.
    """

    default_message: ClassVar[str] = "Loop A/B recording host config is invalid"


class HarnessHostAlreadyStartedError(EvalError):
    """Raised when a started recording host is started a second time.

    The second start would capture the first start's throwaway bootstrap
    secrets as the values to restore, so stopping would leave the operator's
    real environment holding secrets that died with a process.
    """

    default_message: ClassVar[str] = "Loop A/B recording host is already started"


class HarnessDockerUnavailableError(EvalError):
    """Raised when the Docker daemon is unreachable before a recording run.

    Every loop drives a sandbox, so a run without a daemon measures nothing.
    Discovering that inside a cell would first spend real provider tokens and
    then record the failure as that loop's unavailable row, which reads as a
    property of the loop rather than of the machine.
    """

    default_message: ClassVar[str] = "Docker daemon is unreachable"


class HarnessImageUnresolvedError(EvalError):
    """Raised when a declared container image is not on the daemon.

    Its own class rather than a shape of "daemon unavailable", because the
    remedy is different and the machine is fine: a published TAG stopped
    resolving without anything in this repository changing, and the reference
    has to be rebuilt or repointed at a digest.

    Raised after the host has STARTED and before the first paid session,
    which is the only window the question can be asked in. Not earlier:
    unless ``--sandbox-image`` names one, the reference comes from the running
    instance's own settings resolver, so it is not known until the app has
    booted. Not later: a cell plans and writes its contract through the
    gateway, touching no container, so an absent image would first surface at
    grading, by which point the sessions have been paid for and every unit is
    recorded unavailable.
    """

    default_message: ClassVar[str] = "a declared container image does not resolve"


class HarnessProviderDegradedError(EvalError):
    """Raised when a tier's provider is too slow to measure a matrix against.

    Latency is a scored dimension, and cells are recorded one after another
    over roughly an hour, so a provider whose service time swings by an order
    of magnitude scores each cell against whatever its queue was doing rather
    than against the other cells. The probe costs three tiny completions and
    reports the measured figures, so a degraded window fails in seconds instead
    of producing a scoreboard that looks like a comparison and is not.
    """

    default_message: ClassVar[str] = "Provider latency is outside the probe band"


class LoopAbNoCellsMeasuredError(EvalError):
    """Raised when a completed matrix scored no cell at all.

    An all-unavailable scoreboard is never a legitimate measurement, and
    writing one exits successfully with a file that looks like a result. The
    usual cause is a company config whose ``providers`` block does not cover
    the manifest's tiers.
    """

    default_message: ClassVar[str] = "Loop A/B matrix measured no cells"


class OracleUnusableError(EvalError):
    """Raised when a held-out oracle could not produce a verdict at all.

    Deliberately distinct from every requirement failing. A tree that fails
    everything is a measurement; an oracle that could not be collected is a
    broken harness, and recording the second as the first would publish a
    survival curve of zeros that looks exactly like a finding.
    """

    default_message: ClassVar[str] = "The held-out oracle could not be run"


class RecursionDepthClaimUnresolvableError(EvalError):
    """Raised when a planner claim names no requirement the specification has.

    The backstop rather than the primary refusal: the product's own parse
    boundary rejects such a claim where the planning session can still correct
    it, so one reaching the harness means that boundary regressed. Raised
    rather than dropped because dropping is what a recorded sweep did 143
    times, which deflated the ratio it was measuring at both ends and read on
    the chart as a gate that does not help.

    Per cell rather than per sweep: the tree one planner produced is the thing
    at fault, and asked before any leaf runs, so the cell costs its planning
    sessions rather than its whole leaf budget.
    """

    default_message: ClassVar[str] = "A planner claim named no known requirement"


class RecursionDepthSmokeRequiredError(EvalError):
    """Raised when a recording is asked for without a passing wire-level smoke.

    A recording is paid for cell by cell, and every treatment it claims to
    measure was found, once, to have been absent from the engine it ran on
    with nothing able to tell. The one-cell smoke reads each treatment off the
    wire before the matrix is bought, and a recording that cannot show a
    passing smoke for its own manifest digest has not shown it measures what
    it says.
    """

    default_message: ClassVar[str] = (
        "A passing one-cell smoke is required before a recording"
    )


class RecursionDepthNoCellsMeasuredError(EvalError):
    """Raised when a completed recursion-depth sweep measured no cell.

    An all-unavailable report is never a legitimate measurement, and writing
    one exits successfully with a file that looks like a curve.
    """

    default_message: ClassVar[str] = "Recursion-depth sweep measured no cells"


class RecursionDepthSessionCeilingError(EvalError):
    """Raised when a sweep would run more agent sessions than it was allowed.

    The ceiling exists because a depth sweep's session count is a product of
    branching factors nobody can predict from the manifest alone, and the
    failure mode of getting it wrong is spend rather than a wrong answer.
    """

    default_message: ClassVar[str] = "Recursion-depth sweep hit its session ceiling"


class HarnessJournalUnwritableError(EvalError):
    """Raised when a finished cell could not be appended to the journal.

    Systemic rather than per-cell: a journal that cannot be written is true of
    every remaining cell, so a driver treating it as one cell's outcome would
    try to record that outcome to the same broken file. The recording stops
    instead, having kept whatever reached the disk before the failure.
    """

    default_message: ClassVar[str] = "The recording journal could not be written"


class HarnessJournalMismatchError(EvalError):
    """Raised when a recording's journal cannot be appended to or resumed from.

    The records under a journal are real provider spend, so a header naming a
    different matrix or another harness, a corrupted line in the middle, or a
    journal that would be silently overwritten is refused rather than resolved
    by guessing.
    """

    default_message: ClassVar[str] = "The recording journal does not belong to this run"


class RecursionDepthPlannerSubstitutedError(EvalError):
    """Raised when a substitute planner produced the tree a cell would measure.

    The sweep's premise is that recursion here behaves as it does in the
    product, which holds only while the shipped planner writes the plan. The
    substitution is silent everywhere else on purpose: a product that cannot
    plan as an owner is better off with a single-shot plan than with none. A
    measurement is the one caller for which that trade is wrong.

    Carries the session count because it is the one refusal raised with a
    finished tree in hand: every level of it planned and was billed before the
    substitution was noticed, so a caller booking the usual floor of one would
    under-report the spend by everything below the root.

    Args:
        message: Why the tree was refused.
        sessions: How many planning sessions the refused tree cost.
    """

    default_message: ClassVar[str] = (
        "The tree was produced by a substitute planner, not the shipped one"
    )

    def __init__(self, message: str | None = None, *, sessions: int = 1) -> None:
        super().__init__(message)
        self.sessions = sessions


class RecursionDepthJudgeNotIndependentError(EvalError):
    """Raised when the manifest binds the reviewer to the executor's own pair.

    The gate is the treatment in this experiment, so a judge sharing the
    executor's binding biases straight toward the null: self-preference runs
    75-84% toward a model's own family, and an identical pair is that effect at
    its maximum.
    """

    default_message: ClassVar[str] = (
        "The reviewer pair must differ from the executor pair"
    )


class RecursionDepthGateUnbuildableError(EvalError):
    """Raised when the completion-oracle seed came back without its store.

    The gated arm reads the reviewer's verdict out of that store, so a seed
    without one is an arm that would review every merge and record nothing, and
    the run would report the ungated curve twice under two names.
    """

    default_message: ClassVar[str] = (
        "The completion-oracle seed built no report repository"
    )


class RecursionDepthCeilingUndeclaredError(EvalError):
    """Raised when a setting the sweep opens to its ceiling has no ceiling.

    Several of the values a sweep arms ARE their setting's declared maximum,
    so the sweep reads that maximum off the definition rather than copying it
    and letting the two drift. Two ways there is nothing to read: the setting
    is absent, or it is present and unbounded. Guessing either would surface
    as a write refused partway through a paid sweep.
    """

    default_message: ClassVar[str] = (
        "A setting the sweep opens to its ceiling is absent or unbounded"
    )


class RecursionDepthCapabilityUnresolvedError(EvalError):
    """Raised when a sweep cannot resolve the one capability policy.

    Selection and dispatch both read it, so without it the gated arm staffs no
    reviewer and records escalations where it should record verdicts.
    """

    default_message: ClassVar[str] = "No capability policy could be built for the sweep"


class RecursionDepthSpendRepairEmptyError(EvalError):
    """Raised when a spend repair placed none of the log's calls.

    The caveat the repaired report carries is a provenance claim, so a repair
    that attributed nothing would ship a byte-identical report saying its token
    column was rebuilt. Refusing names the log instead, which is the one thing
    that can be wrong here: a path pointing at the wrong run, or a rendering
    the parser no longer matches.
    """

    default_message: ClassVar[str] = "Spend repair attributed no calls to any unit"


class RecursionDepthSpendAlreadyAdoptedError(EvalError):
    """Raised when a repair would overwrite a recording's own raw ledger.

    A second repair reads the ledger the first one wrote, so adopting it again
    would move REPAIRED figures on top of the raw journal that was kept
    precisely so a reader could check the claim. The rows under it are real
    spend and cannot be re-derived from the log, which produces repaired
    figures by construction. Refusing names the file, because the operator who
    meant the second repair can move it aside and the one who did not has just
    been told what they were about to destroy.
    """

    default_message: ClassVar[str] = (
        "This recording's spend column was already repaired"
    )


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
    "HarnessBindHostUnresolvedError",
    "HarnessDockerUnavailableError",
    "HarnessGatewayUnavailableError",
    "HarnessHostAlreadyStartedError",
    "HarnessHostConfigInvalidError",
    "HarnessImageUnresolvedError",
    "HarnessJournalMismatchError",
    "HarnessJournalUnwritableError",
    "HarnessProviderDegradedError",
    "HarnessProviderMissingError",
    "JudgeAnchorSetTooSmallError",
    "JudgeCalibrationFailedError",
    "LoopAbNoCellsMeasuredError",
    "OracleUnusableError",
    "ProvenanceUnavailableError",
    "RecursionDepthCapabilityUnresolvedError",
    "RecursionDepthCeilingUndeclaredError",
    "RecursionDepthClaimUnresolvableError",
    "RecursionDepthGateUnbuildableError",
    "RecursionDepthJudgeNotIndependentError",
    "RecursionDepthNoCellsMeasuredError",
    "RecursionDepthPlannerSubstitutedError",
    "RecursionDepthSessionCeilingError",
    "RecursionDepthSpendAlreadyAdoptedError",
    "RecursionDepthSpendRepairEmptyError",
    "ResearchBriefUnsupportedError",
    "WorkspacePathEscapeError",
    "WorkspaceSeedNotFoundError",
    "WorkspaceSpecMissingError",
]
