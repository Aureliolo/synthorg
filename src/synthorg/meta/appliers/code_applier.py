"""Code applier.

Applies approved code modification proposals by writing files
locally for CI validation, then pushing via the GitHub REST API
and creating a draft PR for human review.

No local ``git`` or ``gh`` CLI is required -- all remote operations
use the GitHub API, making this safe to run inside containers.
"""

import asyncio
from pathlib import Path
from typing import ClassVar

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.meta.appliers._code_io import (
    _apply_single_change,
    _is_within,
    _revert_single_change,
)
from synthorg.meta.appliers._code_validation import (
    _build_pr_body,
    _check_proposal_shape,
    _validate_change_preconditions,
)
from synthorg.meta.config import CodeModificationConfig
from synthorg.meta.models import (
    ApplyResult,
    CIValidationResult,
    CodeChange,
    CodeOperation,
    ImprovementProposal,
    ProposalAltitude,
)
from synthorg.meta.protocol import CIValidator, GitHubAPI
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.meta import (
    META_APPLY_COMPLETED,
    META_APPLY_FAILED,
    META_APPLY_PATH_ESCAPE,
    META_CI_VALIDATION_FAILED,
    META_CODE_FILE_WRITTEN,
)

logger = get_logger(__name__)


class PartialWriteError(DomainError):
    """Raised by ``_write_changes`` on partial application failure.

    Carries the ordered subset of ``CodeChange`` instances that were
    successfully written before the error, so the outer ``apply()``
    handler can revert ONLY those files. Without this, defensive
    revert with the full proposal would attempt to undo changes that
    were never made and could clobber files the proposal never touched.
    """

    default_message: ClassVar[str] = "Partial code write failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500

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
        """This applier handles code modification proposals.

        Returns:
            ``ProposalAltitude`` instance.
        """
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
        except Exception as outer_exc:
            reraise_critical(outer_exc)
            # Drop ``logger.exception`` here -- the outer handler
            # runs after the ``_write_changes`` / GitHub-client
            # paths, and the traceback carries full proposal payload
            # + branch / commit metadata in frame-locals.
            log_exception_redacted(
                logger,
                META_APPLY_FAILED,
                outer_exc,
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
            except Exception as revert_exc:
                reraise_critical(revert_exc)
                logger.warning(
                    META_APPLY_FAILED,
                    altitude="code_modification",
                    proposal_id=str(proposal.id),
                    reason="defensive_revert_failed",
                    error_type=type(revert_exc).__name__,
                    error=safe_error_description(revert_exc),
                )
            # Do NOT call ``self._github.delete_branch(branch)`` here.
            # This outer handler also runs when the failure happens
            # BEFORE ``create_branch()`` (e.g. lint / type-check /
            # test failures during ``_apply_pipeline``), in which
            # case ``branch`` only refers to a planned name -- the
            # actual remote branch may belong to a previous run or
            # an unrelated proposal that happens to hash-collide on
            # the 8-char prefix. ``_apply_pipeline`` already owns
            # orphan-branch cleanup along the push / draft-PR paths
            # where ``create_branch()`` IS known to have run, so we
            # leave that responsibility solely to the inner block.
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

        Raises:
            Exception: Raised on the corresponding failure path.
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
            except Exception as revert_exc:
                reraise_critical(revert_exc)
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
        # ``create_branch`` lives INSIDE the cleanup-owned ``try`` so a
        # client-level failure that nevertheless committed the ref
        # remotely (POST returned with the new ref but the await
        # raised) does not leak an orphan branch; the cleanup branch
        # below still deletes it. ``branch_created`` tracks ownership
        # so we never call ``delete_branch`` for a branch this run
        # didn't actually create -- protects against retries hitting
        # an existing branch from a prior aborted invocation.
        branch_created = False
        try:
            await self._github.create_branch(branch)
            branch_created = True
            await self._push_changes_via_api(
                branch,
                proposal,
            )
            pr_url = await self._github.create_draft_pr(
                head=branch,
                title=proposal.title,
                body=_build_pr_body(proposal),
            )
        except Exception as exc:
            reraise_critical(exc)
            # Partial push left an orphaned branch -- clean up only
            # when we know this invocation created it.
            if branch_created:
                try:
                    await self._github.delete_branch(branch)
                except Exception as cleanup_exc:
                    reraise_critical(cleanup_exc)
                    # Same scrub as the other GitHub-client-error
                    # path above.
                    logger.warning(
                        META_APPLY_FAILED,
                        altitude="code_modification",
                        proposal_id=str(proposal.id),
                        reason="branch_cleanup_after_push_failed",
                        branch=branch,
                        error_type=type(cleanup_exc).__name__,
                        error=safe_error_description(cleanup_exc),
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
            PartialWriteError: If applying a change fails after one or
                more changes were written; carries the applied subset
                so the outer ``apply()`` can revert what already landed.
            MemoryError: Raised on the corresponding failure path.
            RecursionError: Raised on the corresponding failure path.
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
                # Carry the applied subset on ``PartialWriteError`` so
                # ``apply()``'s outer revert reaches the writes that
                # already landed before this change tripped the
                # path-escape guard. Raising a plain ``RuntimeError``
                # would leave ``applied_subset=()`` on the outer
                # handler and the partially-written workspace would
                # stay dirty after the failed apply.
                raise PartialWriteError(msg, applied=tuple(applied))
            try:
                _apply_single_change(change, file_path)
            except MemoryError, RecursionError:
                raise
            except (OSError, RuntimeError) as exc:
                # Without this, the ``msg`` and chained-exception
                # paths leak raw ``str(exc)`` into the
                # PartialWriteError that the caller logs via
                # ``logger.exception``, bypassing the secret-log
                # gate once the wrapper re-raises. Sanitize once via
                # ``safe_error_description`` and break the chain with
                # ``from None`` so the original exception cannot
                # resurface.
                scrubbed = safe_error_description(exc)
                logger.warning(
                    META_APPLY_FAILED,
                    reason="file_write_failed",
                    operation=change.operation.value,
                    file_path=change.file_path,
                    error_type=type(exc).__name__,
                    error=scrubbed,
                )
                msg = (
                    f"{change.operation.value} failed for "
                    f"'{change.file_path}': {scrubbed}"
                )
                # Wrap the underlying error so the caller can revert
                # ONLY the changes that were successfully written
                # before the failure -- avoids defensive-revert
                # clobbering files that were never touched.
                raise PartialWriteError(
                    msg,
                    applied=tuple(applied),
                ) from None
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
            except OSError as exc:
                logger.warning(
                    META_APPLY_FAILED,
                    reason="local_revert_failed",
                    file_path=change.file_path,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
