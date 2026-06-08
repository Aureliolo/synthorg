"""Protocols for the self-improving company meta-loop.

Defines the pluggable strategy interfaces for signal aggregation,
improvement strategies, proposal guards, appliers, rollout
strategies, and regression detection.
"""

from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.meta.models import (
    ApplyResult,
    CIValidationResult,
    CodeChange,
    GuardResult,
    ImprovementProposal,
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgScalingSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
    ProposalAltitude,
    RegressionResult,
    RegressionThresholds,
    RolloutResult,
    RuleMatch,
)


@runtime_checkable
class SignalAggregator(Protocol):
    """Aggregates raw signals from a subsystem into a typed summary.

    Each aggregator wraps one existing subsystem (performance tracker,
    budget analytics, coordination metrics, etc.) and produces a
    structured summary for the specified time window.
    """

    @property
    def domain(self) -> NotBlankStr:
        """Signal domain name (e.g. 'performance', 'budget')."""
        ...

    async def aggregate(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> (
        OrgPerformanceSummary
        | OrgBudgetSummary
        | OrgCoordinationSummary
        | OrgScalingSummary
        | OrgErrorSummary
        | OrgEvolutionSummary
        | OrgTelemetrySummary
    ):
        """Collect and aggregate signals for the time window.

        Args:
            since: Start of the observation window (UTC).
            until: End of the observation window (UTC).

        Returns:
            Domain-specific typed summary model.
        """
        ...


@runtime_checkable
class SignalRule(Protocol):
    """Detects patterns in signals that warrant improvement analysis.

    Rules are deterministic and cheap. When a rule fires, it triggers
    a scoped LLM analysis call through the appropriate strategy.
    """

    @property
    def name(self) -> NotBlankStr:
        """Human-readable rule name."""
        ...

    @property
    def target_altitudes(self) -> tuple[ProposalAltitude, ...]:
        """Which improvement altitudes this rule suggests."""
        ...

    def evaluate(self, snapshot: OrgSignalSnapshot) -> RuleMatch | None:
        """Evaluate the snapshot against this rule.

        Args:
            snapshot: Current org-wide signal snapshot.

        Returns:
            A RuleMatch if the pattern was detected, None otherwise.
        """
        ...


@runtime_checkable
class ImprovementStrategy(Protocol):
    """Generates improvement proposals from signals and rule triggers.

    Each strategy handles one proposal altitude (config tuning,
    architecture, or prompt tuning) and uses LLM calls to synthesize
    concrete proposals from the signal context.
    """

    @property
    def altitude(self) -> ProposalAltitude:
        """Which proposal altitude this strategy produces."""
        ...

    async def propose(
        self,
        *,
        snapshot: OrgSignalSnapshot,
        triggered_rules: tuple[RuleMatch, ...],
    ) -> tuple[ImprovementProposal, ...]:
        """Generate improvement proposals from signals and rules.

        Args:
            snapshot: Current org-wide signal snapshot.
            triggered_rules: Rules that fired for this strategy's altitude.

        Returns:
            Tuple of proposals (empty if no improvements suggested).
        """
        ...


@runtime_checkable
class ProposalGuard(Protocol):
    """Validates a proposal before it is routed for approval.

    Guards form a sequential chain. All must pass for a proposal
    to reach the approval queue.
    """

    @property
    def name(self) -> NotBlankStr:
        """Human-readable guard name."""
        ...

    async def evaluate(
        self,
        proposal: ImprovementProposal,
    ) -> GuardResult:
        """Evaluate whether a proposal should proceed.

        Args:
            proposal: The improvement proposal to evaluate.

        Returns:
            Guard result with verdict and optional reason.
        """
        ...


@runtime_checkable
class ProposalApplier(Protocol):
    """Applies an approved proposal to the running system.

    Each applier handles one proposal altitude. It can both
    apply changes and perform dry-run validation.
    """

    @property
    def altitude(self) -> ProposalAltitude:
        """Which proposal altitude this applier handles."""
        ...

    async def apply(
        self,
        proposal: ImprovementProposal,
    ) -> ApplyResult:
        """Apply the proposal's changes to the system.

        Args:
            proposal: The approved proposal to apply.

        Returns:
            Result indicating success or failure.
        """
        ...

    async def dry_run(
        self,
        proposal: ImprovementProposal,
    ) -> ApplyResult:
        """Validate the proposal without applying changes.

        Args:
            proposal: The proposal to validate.

        Returns:
            Result indicating whether the apply would succeed.
        """
        ...


@runtime_checkable
class RolloutStrategy(Protocol):
    """Controls how an approved proposal is deployed.

    Implementations include before/after comparison and
    canary subset rollout.
    """

    @property
    def name(self) -> NotBlankStr:
        """Human-readable rollout strategy name."""
        ...

    async def execute(
        self,
        *,
        proposal: ImprovementProposal,
        applier: ProposalApplier,
        detector: RegressionDetector,
    ) -> RolloutResult:
        """Execute the rollout for an approved proposal.

        Args:
            proposal: The approved proposal to roll out.
            applier: Applier for the proposal's altitude.
            detector: Regression detector for post-apply monitoring.

        Returns:
            Rollout result with outcome and regression details.
        """
        ...


@runtime_checkable
class RegressionDetector(Protocol):
    """Detects metric regression after proposal application.

    Implementations include threshold-based circuit-breakers
    and statistical significance testing.
    """

    @property
    def name(self) -> NotBlankStr:
        """Human-readable detector name."""
        ...

    async def check(
        self,
        *,
        baseline: OrgSignalSnapshot,
        current: OrgSignalSnapshot,
        thresholds: RegressionThresholds,
    ) -> RegressionResult:
        """Check for regression between baseline and current signals.

        Args:
            baseline: Signal snapshot from before the change.
            current: Signal snapshot from after the change.
            thresholds: Configurable degradation thresholds.

        Returns:
            Regression result with verdict and details.
        """
        ...


@runtime_checkable
class GitHubAPI(Protocol):
    """Pushes code changes to a GitHub repository.

    Used by ``CodeApplier`` to create branches, push file changes,
    open draft PRs, and clean up branches -- all via the GitHub
    REST API so no local ``git`` or ``gh`` CLI is required.
    """

    async def create_branch(self, name: str) -> None:
        """Create a new branch from the default branch HEAD.

        Args:
            name: Branch name to create.
        """
        ...

    async def push_change(
        self,
        *,
        branch: str,
        change: CodeChange,
        message: str,
    ) -> None:
        """Push a single file change (create/modify/delete) to a branch.

        Args:
            branch: Target branch name.
            change: The code change to push.
            message: Commit message.
        """
        ...

    async def create_draft_pr(
        self,
        *,
        head: str,
        title: str,
        body: str,
    ) -> str:
        """Create a draft pull request.

        Args:
            head: Head branch name.
            title: PR title.
            body: PR body (Markdown).

        Returns:
            URL of the created PR.
        """
        ...

    async def delete_branch(self, name: str) -> None:
        """Delete a remote branch.

        Args:
            name: Branch name to delete.
        """
        ...

    async def verify_token(self) -> None:
        """Verify that the configured token is valid.

        Raises:
            GitHubAuthError: If the token is invalid or expired.
            GitHubAPIError: On other API failures.
        """
        ...


# Default impl in meta/validation/ci_validator.py; injected via
# meta/factory.py and consumed by meta/appliers/code_applier.py.
@runtime_checkable
class CIValidator(Protocol):
    """Validates proposed code changes against CI checks.

    Implementations run lint, type-check, and test commands
    against the changed files and report aggregate results.
    """

    async def validate(
        self,
        *,
        project_root: Path,
        changed_files: tuple[str, ...],
    ) -> CIValidationResult:
        """Run CI validation against changed files.

        Args:
            project_root: Absolute path to the project root.
            changed_files: Relative paths of files that changed.

        Returns:
            CI validation result with per-step outcomes.
        """
        ...
