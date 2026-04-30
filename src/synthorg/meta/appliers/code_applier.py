"""Code applier.

Applies approved code modification proposals by writing files
locally for CI validation, then pushing via the GitHub REST API
and creating a draft PR for human review.

No local ``git`` or ``gh`` CLI is required -- all remote operations
use the GitHub API, making this safe to run inside containers.
"""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from synthorg.meta.models import (
    ApplyResult,
    CIValidationResult,
    CodeChange,
    CodeOperation,
    ImprovementProposal,
    ProposalAltitude,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import (
    META_APPLY_COMPLETED,
    META_APPLY_CREATE_TARGET_EXISTS,
    META_APPLY_DELETE_CONTENT_DRIFT,
    META_APPLY_DELETE_TARGET_MISSING,
    META_APPLY_FAILED,
    META_APPLY_MODIFY_CONTENT_DRIFT,
    META_APPLY_MODIFY_TARGET_MISSING,
    META_APPLY_PATH_ESCAPE,
    META_CI_VALIDATION_FAILED,
    META_CODE_FILE_WRITTEN,
)

if TYPE_CHECKING:
    from synthorg.meta.config import CodeModificationConfig
    from synthorg.meta.protocol import CIValidator, GitHubAPI

logger = get_logger(__name__)


class PartialWriteError(RuntimeError):
    """Raised by ``_write_changes`` on partial application failure.

    Carries the ordered subset of ``CodeChange`` instances that were
    successfully written before the error, so the outer ``apply()``
    handler can revert ONLY those files. Without this, defensive
    revert with the full proposal would attempt to undo changes that
    were never made and could clobber files the proposal never touched.
    """

    def __init__(self, message: str, *, applied: tuple[CodeChange, ...]) -> None:
        super().__init__(message)
        self.applied = applied


class CodeApplier:
    """Applies code modification proposals.

    Writes proposed changes locally for CI validation, then pushes
    them to GitHub via the REST API and opens a draft PR.
    Does NOT auto-merge -- human review is mandatory.

    Args:
        ci_validator: CI validator for lint/type-check/test checks.
        github_client: GitHub API client for branch/file/PR operations.
        code_modification_config: Code modification settings.
    """

    def __init__(
        self,
        *,
        ci_validator: CIValidator,
        github_client: GitHubAPI,
        code_modification_config: CodeModificationConfig,
    ) -> None:
        self._ci_validator = ci_validator
        self._github = github_client
        self._config = code_modification_config
        self._project_root = (
            Path(str(code_modification_config.project_root))
            if code_modification_config.project_root
            else None
        )

    @property
    def altitude(self) -> ProposalAltitude:
        """This applier handles code modification proposals."""
        return ProposalAltitude.CODE_MODIFICATION

    async def verify_github_token(self) -> None:
        """Verify that the configured GitHub token is valid.

        Raises:
            GitHubAuthError: On 401/403.
            GitHubAPIError: On other failures.
        """
        await self._github.verify_token()

    async def aclose(self) -> None:
        """Close the underlying GitHub client if it supports it."""
        close = getattr(self._github, "aclose", None)
        if close is not None:
            await close()

    async def apply(
        self,
        proposal: ImprovementProposal,
    ) -> ApplyResult:
        """Apply code changes: local CI, then push via GitHub API.

        Args:
            proposal: The approved code modification proposal.

        Returns:
            Result indicating success or failure.
        """
        error = _check_proposal_shape(proposal)
        if error is not None:
            return error
        project_root = self._project_root or Path.cwd()
        branch = f"{self._config.branch_prefix}/{str(proposal.id)[:8]}"
        try:
            return await self._apply_pipeline(
                proposal,
                branch,
                project_root,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as outer_exc:
            logger.exception(
                META_APPLY_FAILED,
                altitude="code_modification",
                proposal_id=str(proposal.id),
            )
            # Revert ONLY the changes that were actually written. If
            # the failure surfaced from ``_write_changes`` it carries
            # the applied-so-far subset on a ``PartialWriteError``;
            # any other failure path means the inner finally already
            # reverted (so an empty subset is the safe default).
            applied_subset: tuple[CodeChange, ...] = ()
            if isinstance(outer_exc, PartialWriteError):
                applied_subset = outer_exc.applied
            try:
                await asyncio.to_thread(
                    self._revert_local_changes,
                    applied_subset,
                    project_root,
                    defensive=True,
                )
            except MemoryError, RecursionError:
                raise
            except Exception as revert_exc:
                logger.warning(
                    META_APPLY_FAILED,
                    altitude="code_modification",
                    proposal_id=str(proposal.id),
                    reason="defensive_revert_failed",
                    error_type=type(revert_exc).__name__,
                    error=safe_error_description(revert_exc),
                )
            try:
                await self._github.delete_branch(branch)
            except Exception:
                logger.exception(
                    META_APPLY_FAILED,
                    altitude="code_modification",
                    proposal_id=str(proposal.id),
                    reason="cleanup_failed",
                    branch=branch,
                )
            return ApplyResult(
                success=False,
                error_message="Code apply failed. Check logs for details.",
                changes_applied=0,
            )

    async def _apply_pipeline(
        self,
        proposal: ImprovementProposal,
        branch: str,
        project_root: Path,
    ) -> ApplyResult:
        """Execute the apply pipeline.

        1. Write files locally for CI validation.
        2. Run lint / type-check / tests.
        3. Push changes to GitHub via API.
        4. Create a draft PR.
        5. Revert local file changes.

        Args:
            proposal: The approved proposal.
            branch: Git branch name.
            project_root: Absolute path to project root.

        Returns:
            Result indicating success or failure.
        """
        # -- Local CI gate ------------------------------------------------
        # Filesystem mutations run on a worker thread (``asyncio.to_thread``)
        # so the per-change ``Path.read_text`` / ``Path.write_text`` /
        # ``Path.unlink`` calls don't block the event loop while CI gates
        # for other proposals progress concurrently.
        changed_files, applied = await asyncio.to_thread(
            self._write_changes,
            proposal.code_changes,
            project_root,
        )
        try:
            ci_result = await self._run_ci(
                proposal,
                changed_files,
                project_root,
            )
        finally:
            # Revert only the changes that were actually written. The
            # cleanup itself is wrapped so a transient I/O error during
            # revert (e.g. permission flake) does not mask the CI
            # outcome captured in ``ci_result`` -- the warning surfaces
            # the cleanup failure for ops without overwriting the
            # primary success/failure signal.
            try:
                await asyncio.to_thread(
                    self._revert_local_changes,
                    applied,
                    project_root,
                )
            except MemoryError, RecursionError:
                raise
            except Exception as revert_exc:
                logger.warning(
                    META_APPLY_FAILED,
                    altitude="code_modification",
                    proposal_id=str(proposal.id),
                    reason="cleanup_revert_failed",
                    error_type=type(revert_exc).__name__,
                    error=safe_error_description(revert_exc),
                )
        if not ci_result.passed:
            return ApplyResult(
                success=False,
                error_message=(f"CI validation failed: {'; '.join(ci_result.errors)}"),
                changes_applied=0,
            )

        # -- Remote push via GitHub API -----------------------------------
        await self._github.create_branch(branch)
        try:
            await self._push_changes_via_api(
                branch,
                proposal,
            )
            pr_url = await self._github.create_draft_pr(
                head=branch,
                title=proposal.title,
                body=_build_pr_body(proposal),
            )
        except MemoryError, RecursionError:
            raise
        except Exception:
            # Partial push left an orphaned branch -- clean up.
            try:
                await self._github.delete_branch(branch)
            except Exception:
                logger.exception(
                    META_APPLY_FAILED,
                    altitude="code_modification",
                    proposal_id=str(proposal.id),
                    reason="branch_cleanup_after_push_failed",
                    branch=branch,
                )
            raise

        count = len(proposal.code_changes)
        logger.info(
            META_APPLY_COMPLETED,
            altitude="code_modification",
            changes=count,
            proposal_id=str(proposal.id),
            branch=branch,
            pr_url=pr_url,
        )
        return ApplyResult(success=True, changes_applied=count)

    async def _run_ci(
        self,
        proposal: ImprovementProposal,
        changed_files: list[str],
        project_root: Path,
    ) -> CIValidationResult:
        """Run CI validation against locally written files.

        Args:
            proposal: The proposal being validated.
            changed_files: Relative paths of changed files.
            project_root: Absolute path to project root.

        Returns:
            CI validation result.
        """
        # Exclude deleted paths -- ruff/mypy fail on missing files.
        delete_paths = {
            c.file_path
            for c in proposal.code_changes
            if c.operation == CodeOperation.DELETE
        }
        ci_files = tuple(f for f in changed_files if f not in delete_paths)
        ci_result = await self._ci_validator.validate(
            project_root=project_root,
            changed_files=ci_files,
        )
        if not ci_result.passed:
            logger.warning(
                META_CI_VALIDATION_FAILED,
                proposal_id=str(proposal.id),
                errors=list(ci_result.errors),
            )
        return ci_result

    async def _push_changes_via_api(
        self,
        branch: str,
        proposal: ImprovementProposal,
    ) -> None:
        """Push all file changes to GitHub via the REST API.

        Args:
            branch: Target branch name.
            proposal: The proposal whose code changes to push.
        """
        for change in proposal.code_changes:
            await self._github.push_change(
                branch=branch,
                change=change,
                message=(f"feat: {change.description}\n\nProposal: {proposal.id}"),
            )

    async def dry_run(
        self,
        proposal: ImprovementProposal,
    ) -> ApplyResult:
        """Validate code changes without applying.

        Checks operation consistency and target file existence
        for modify/delete operations.

        Args:
            proposal: The proposal to validate.

        Returns:
            Result indicating whether apply would succeed.
        """
        error = _check_proposal_shape(proposal)
        if error is not None:
            return error
        project_root = self._project_root or Path.cwd()
        errors: list[str] = []

        resolved_root = project_root.resolve()
        for change in proposal.code_changes:
            file_path = project_root / change.file_path
            if not _is_within(file_path, resolved_root):
                errors.append(
                    f"Path escapes project root: {change.file_path}",
                )
                continue
            _validate_change_preconditions(change, file_path, errors)

        if errors:
            return ApplyResult(
                success=False,
                error_message="; ".join(errors),
                changes_applied=0,
            )
        return ApplyResult(
            success=True,
            changes_applied=len(proposal.code_changes),
        )

    @staticmethod
    def _write_changes(
        changes: tuple[CodeChange, ...],
        project_root: Path,
    ) -> tuple[list[str], tuple[CodeChange, ...]]:
        """Write code changes to disk for local CI validation.

        Args:
            changes: Code changes to apply.
            project_root: Absolute path to project root.

        Returns:
            Tuple of (relative file paths, applied CodeChange objects).
            On partial failure the applied tuple contains only the
            changes that were successfully written before the error.

        Raises:
            RuntimeError: If a file write or delete fails.
        """
        changed: list[str] = []
        applied: list[CodeChange] = []
        resolved_root = project_root.resolve()
        for change in changes:
            file_path = project_root / change.file_path
            if not _is_within(file_path, resolved_root):
                logger.error(
                    META_APPLY_PATH_ESCAPE,
                    file_path=change.file_path,
                    project_root=str(resolved_root),
                )
                msg = f"Path escapes project root: {change.file_path}"
                raise RuntimeError(msg)
            try:
                _apply_single_change(change, file_path)
            except MemoryError, RecursionError:
                raise
            except (OSError, RuntimeError) as exc:
                logger.warning(
                    META_APPLY_FAILED,
                    reason="file_write_failed",
                    operation=change.operation.value,
                    file_path=change.file_path,
                    error=str(exc),
                )
                msg = f"{change.operation.value} failed for '{change.file_path}': {exc}"
                # Wrap the underlying error so the caller can revert
                # ONLY the changes that were successfully written
                # before the failure -- avoids defensive-revert
                # clobbering files that were never touched.
                raise PartialWriteError(
                    msg,
                    applied=tuple(applied),
                ) from exc
            applied.append(change)
            changed.append(change.file_path)
            logger.debug(
                META_CODE_FILE_WRITTEN,
                operation=change.operation.value,
                file_path=change.file_path,
            )
        return changed, tuple(applied)

    @staticmethod
    def _revert_local_changes(
        changes: tuple[CodeChange, ...],
        project_root: Path,
        *,
        defensive: bool = False,
    ) -> None:
        """Revert locally written file changes.

        For each change, restores the file to its pre-proposal state:
        CREATE -> delete the file, MODIFY -> restore old_content,
        DELETE -> recreate with old_content.

        Args:
            changes: The code changes to revert.
            project_root: Absolute path to project root.
            defensive: If True, skip reverts where the file state
                doesn't match expectations (used in outer exception
                handler where applied set is unknown).
        """
        resolved_root = project_root.resolve()
        for change in changes:
            path = project_root / change.file_path
            if not _is_within(path, resolved_root):
                continue
            try:
                _revert_single_change(change, path, defensive=defensive)
            except OSError:
                logger.warning(
                    META_APPLY_FAILED,
                    reason="local_revert_failed",
                    file_path=change.file_path,
                )


def _apply_single_change(change: CodeChange, file_path: Path) -> None:
    """Write a single code change to disk with precondition checks.

    Validates filesystem state before mutating to avoid clobbering
    files that changed since proposal generation.

    Args:
        change: The code change descriptor.
        file_path: Absolute path to write.

    Raises:
        RuntimeError: If preconditions are violated.
    """
    if change.operation == CodeOperation.CREATE:
        if file_path.exists():
            logger.error(
                META_APPLY_CREATE_TARGET_EXISTS,
                file_path=change.file_path,
            )
            msg = f"CREATE target already exists: {change.file_path}"
            raise RuntimeError(msg)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(change.new_content, encoding="utf-8")
    elif change.operation == CodeOperation.MODIFY:
        if not file_path.exists():
            logger.error(
                META_APPLY_MODIFY_TARGET_MISSING,
                file_path=change.file_path,
            )
            msg = f"MODIFY target does not exist: {change.file_path}"
            raise RuntimeError(msg)
        current = file_path.read_text(encoding="utf-8")
        if current != change.old_content:
            logger.error(
                META_APPLY_MODIFY_CONTENT_DRIFT,
                file_path=change.file_path,
            )
            msg = f"MODIFY target changed since proposal generation: {change.file_path}"
            raise RuntimeError(msg)
        file_path.write_text(change.new_content, encoding="utf-8")
    elif change.operation == CodeOperation.DELETE:
        if not file_path.exists():
            logger.error(
                META_APPLY_DELETE_TARGET_MISSING,
                file_path=change.file_path,
            )
            msg = f"DELETE target does not exist: {change.file_path}"
            raise RuntimeError(msg)
        current = file_path.read_text(encoding="utf-8")
        if current != change.old_content:
            logger.error(
                META_APPLY_DELETE_CONTENT_DRIFT,
                file_path=change.file_path,
            )
            msg = f"DELETE target changed since proposal generation: {change.file_path}"
            raise RuntimeError(msg)
        file_path.unlink()


def _check_proposal_shape(
    proposal: ImprovementProposal,
) -> ApplyResult | None:
    """Reject proposals with wrong altitude or no changes.

    Args:
        proposal: The proposal to validate.

    Returns:
        A failure ApplyResult if invalid, or None if OK.
    """
    if proposal.altitude != ProposalAltitude.CODE_MODIFICATION:
        return ApplyResult(
            success=False,
            error_message=(
                f"Expected CODE_MODIFICATION altitude, got {proposal.altitude.value}"
            ),
            changes_applied=0,
        )
    if not proposal.code_changes:
        return ApplyResult(
            success=False,
            error_message="Proposal has no code changes",
            changes_applied=0,
        )
    return None


def _validate_change_preconditions(
    change: CodeChange,
    file_path: Path,
    errors: list[str],
) -> None:
    """Check filesystem preconditions for a single code change.

    Args:
        change: The code change to validate.
        file_path: Resolved absolute path.
        errors: Mutable list to append error descriptions to.
    """
    if change.operation == CodeOperation.MODIFY:
        if not file_path.exists():
            errors.append(
                f"MODIFY target does not exist: {change.file_path}",
            )
        else:
            current = file_path.read_text(encoding="utf-8")
            if current != change.old_content:
                errors.append(
                    f"MODIFY target changed since proposal: {change.file_path}",
                )
    elif change.operation == CodeOperation.DELETE:
        if not file_path.exists():
            errors.append(
                f"DELETE target does not exist: {change.file_path}",
            )
        else:
            current = file_path.read_text(encoding="utf-8")
            if current != change.old_content:
                errors.append(
                    f"DELETE target changed since proposal: {change.file_path}",
                )
    elif change.operation == CodeOperation.CREATE and file_path.exists():
        errors.append(
            f"CREATE target already exists: {change.file_path}",
        )


def _is_within(candidate: Path, root: Path) -> bool:
    """Check that candidate resolves to a path inside root.

    Args:
        candidate: Path to validate.
        root: Already-resolved project root.

    Returns:
        True if candidate is a descendant of root.
    """
    try:
        candidate.resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _revert_single_change(  # noqa: C901 -- linear branch tree per CodeOperation, splitting hides the parallel structure
    change: CodeChange,
    path: Path,
    *,
    defensive: bool,
) -> None:
    """Revert a single local file change.

    Args:
        change: The code change to undo.
        path: Absolute file path.
        defensive: Skip revert if file state doesn't indicate the
            change was applied (prevents overwriting untouched files).
    """
    if change.operation == CodeOperation.CREATE:
        if defensive:
            # Only delete if the file's current contents match what
            # ``_apply_single_change`` would have written. Without this
            # check, defensive revert of a CREATE proposal whose target
            # accidentally pre-existed (caused the precondition failure
            # in ``_apply_single_change``) would clobber the operator's
            # pre-existing file.
            if not path.exists():
                return
            current = path.read_text(encoding="utf-8")
            if current != change.new_content:
                return
        path.unlink(missing_ok=True)
    elif change.operation == CodeOperation.MODIFY:
        if defensive:
            if not path.exists():
                return
            # Only revert if content matches new_content (was applied).
            current = path.read_text(encoding="utf-8")
            if current != change.new_content:
                return
        path.write_text(change.old_content, encoding="utf-8")
    elif change.operation == CodeOperation.DELETE:
        if defensive and path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(change.old_content, encoding="utf-8")


def _build_pr_body(proposal: ImprovementProposal) -> str:
    """Build the PR body from a proposal.

    Args:
        proposal: The proposal to describe.

    Returns:
        Markdown-formatted PR body.
    """
    return (
        f"## Meta-Loop Code Modification\n\n"
        f"**Proposal ID**: {proposal.id}\n"
        f"**Source Rule**: {proposal.source_rule}\n"
        f"**Confidence**: {proposal.confidence:.0%}\n\n"
        f"### Rationale\n\n"
        f"{proposal.rationale.signal_summary}\n\n"
        f"### Changes\n\n"
        f"{proposal.description}\n\n"
        f"---\n"
        f"*Auto-generated by the self-improvement meta-loop. "
        f"Human review required before merge.*"
    )
