"""Unit tests for code modification applier."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.meta.appliers.code_applier import (
    CodeApplier,
    PartialWriteError,
)
from synthorg.meta.appliers.github_client import (
    GitHubAPIError,
    GitHubAuthError,
    _sanitize_response_body,
)
from synthorg.meta.config import CodeModificationConfig
from synthorg.meta.models import (
    CIValidationResult,
    CodeChange,
    CodeOperation,
    ImprovementProposal,
    ProposalAltitude,
    ProposalRationale,
    RollbackOperation,
    RollbackPlan,
)

pytestmark = pytest.mark.unit


def _rationale() -> ProposalRationale:
    return ProposalRationale(
        signal_summary="test",
        pattern_detected="test",
        expected_impact="test",
        confidence_reasoning="test",
    )


def _rollback() -> RollbackPlan:
    return RollbackPlan(
        operations=(
            RollbackOperation(
                operation_type="revert_branch",
                target="meta/code-mod/test",
                description="revert",
            ),
        ),
        validation_check="branch deleted",
    )


def _code_proposal(
    *,
    changes: tuple[CodeChange, ...] | None = None,
) -> ImprovementProposal:
    if changes is None:
        changes = (
            CodeChange(
                file_path="src/synthorg/meta/strategies/new.py",
                operation=CodeOperation.CREATE,
                new_content="class New:\n    pass\n",
                description="Add new strategy",
                reasoning="Quality declining",
            ),
        )
    return ImprovementProposal(
        altitude=ProposalAltitude.CODE_MODIFICATION,
        title="Test code proposal",
        description="Test description",
        rationale=_rationale(),
        code_changes=changes,
        rollback_plan=_rollback(),
        confidence=0.6,
        source_rule="quality_declining",
    )


def _ci_pass() -> CIValidationResult:
    return CIValidationResult(
        passed=True,
        lint_passed=True,
        typecheck_passed=True,
        tests_passed=True,
        duration_seconds=5.0,
    )


def _ci_fail() -> CIValidationResult:
    return CIValidationResult(
        passed=False,
        lint_passed=False,
        typecheck_passed=True,
        tests_passed=True,
        errors=("lint: E501 line too long",),
        duration_seconds=2.0,
    )


def _mock_ci_validator(
    result: CIValidationResult | None = None,
) -> AsyncMock:
    ci = AsyncMock()
    ci.validate = AsyncMock(return_value=result or _ci_pass())
    return ci


def _mock_github_client() -> AsyncMock:
    """Mock GitHubAPI that succeeds on all operations."""
    gh = AsyncMock()
    gh.create_branch = AsyncMock()
    gh.push_change = AsyncMock()
    gh.create_draft_pr = AsyncMock(
        return_value="https://github.com/test/repo/pull/99",
    )
    gh.delete_branch = AsyncMock()
    gh.verify_token = AsyncMock()
    gh.aclose = AsyncMock()
    return gh


class TestCodeApplier:
    """Code applier tests."""

    def test_altitude(self) -> None:
        applier = CodeApplier(
            ci_validator=_mock_ci_validator(),
            github_client=_mock_github_client(),
            code_modification_config=CodeModificationConfig(),
        )
        assert applier.altitude == ProposalAltitude.CODE_MODIFICATION

    async def test_apply_success(self, tmp_path: Path) -> None:
        ci = _mock_ci_validator(_ci_pass())
        gh = _mock_github_client()
        applier = CodeApplier(
            ci_validator=ci,
            github_client=gh,
            code_modification_config=CodeModificationConfig(),
        )
        proposal = _code_proposal()

        with patch(
            "synthorg.meta.appliers.code_applier.Path.cwd",
            return_value=tmp_path,
        ):
            strategies_dir = tmp_path / "src" / "synthorg" / "meta" / "strategies"
            strategies_dir.mkdir(parents=True)
            result = await applier.apply(proposal)

        assert result.success
        assert result.changes_applied == 1
        assert result.error_message is None
        # GitHub API was called with proposal data.
        gh.create_branch.assert_awaited_once()
        gh.push_change.assert_awaited_once()
        push_args = gh.push_change.call_args
        assert push_args is not None
        gh.create_draft_pr.assert_awaited_once()
        # Local file was reverted after push.
        written = strategies_dir / "new.py"
        assert not written.exists()

    async def test_apply_ci_failure_reverts_local(
        self,
        tmp_path: Path,
    ) -> None:
        ci = _mock_ci_validator(_ci_fail())
        gh = _mock_github_client()
        applier = CodeApplier(
            ci_validator=ci,
            github_client=gh,
            code_modification_config=CodeModificationConfig(),
        )
        proposal = _code_proposal()

        with patch(
            "synthorg.meta.appliers.code_applier.Path.cwd",
            return_value=tmp_path,
        ):
            strategies_dir = tmp_path / "src" / "synthorg" / "meta" / "strategies"
            strategies_dir.mkdir(parents=True)
            result = await applier.apply(proposal)

        assert not result.success
        assert result.changes_applied == 0
        assert "CI validation failed" in (result.error_message or "")
        # GitHub API was NOT called (CI failed).
        gh.create_branch.assert_not_awaited()
        # Local file was reverted.
        written = strategies_dir / "new.py"
        assert not written.exists()

    async def test_apply_github_failure_cleans_up(
        self,
        tmp_path: Path,
    ) -> None:
        """``create_branch`` failure is a no-cleanup path (#1682).

        Pre-PR review #1693 (CodeRabbit critical at code_applier.py:189):
        the outer exception handler used to call ``delete_branch``
        unconditionally, which would clobber a stale remote branch
        from a previous run when ``create_branch()`` itself raised.
        The fix removes the outer delete; ``_apply_pipeline`` keeps
        owning orphan-branch cleanup along the push / draft-PR
        paths where ``create_branch()`` is known to have run.
        """
        ci = _mock_ci_validator(_ci_pass())
        gh = _mock_github_client()
        gh.create_branch = AsyncMock(
            side_effect=RuntimeError("GitHub API failed"),
        )
        applier = CodeApplier(
            ci_validator=ci,
            github_client=gh,
            code_modification_config=CodeModificationConfig(),
        )
        proposal = _code_proposal()

        with patch(
            "synthorg.meta.appliers.code_applier.Path.cwd",
            return_value=tmp_path,
        ):
            strategies_dir = tmp_path / "src" / "synthorg" / "meta" / "strategies"
            strategies_dir.mkdir(parents=True)
            result = await applier.apply(proposal)

        assert not result.success
        assert "Code apply failed" in (result.error_message or "")
        # ``create_branch`` raised -- no remote branch was created,
        # so ``delete_branch`` MUST NOT fire (would clobber a stale
        # branch from a previous run).
        gh.delete_branch.assert_not_awaited()

    async def test_apply_pr_creation_failure(
        self,
        tmp_path: Path,
    ) -> None:
        ci = _mock_ci_validator(_ci_pass())
        gh = _mock_github_client()
        gh.create_draft_pr = AsyncMock(
            side_effect=RuntimeError("auth required"),
        )
        applier = CodeApplier(
            ci_validator=ci,
            github_client=gh,
            code_modification_config=CodeModificationConfig(),
        )
        proposal = _code_proposal()

        with patch(
            "synthorg.meta.appliers.code_applier.Path.cwd",
            return_value=tmp_path,
        ):
            strategies_dir = tmp_path / "src" / "synthorg" / "meta" / "strategies"
            strategies_dir.mkdir(parents=True)
            result = await applier.apply(proposal)

        assert not result.success
        assert "Code apply failed" in (result.error_message or "")

    async def test_dry_run_create_valid(self, tmp_path: Path) -> None:
        applier = CodeApplier(
            ci_validator=_mock_ci_validator(),
            github_client=_mock_github_client(),
            code_modification_config=CodeModificationConfig(),
        )
        proposal = _code_proposal()
        with patch(
            "synthorg.meta.appliers.code_applier.Path.cwd",
            return_value=tmp_path,
        ):
            result = await applier.dry_run(proposal)
        assert result.success
        assert result.changes_applied == 1

    async def test_dry_run_modify_missing_file(
        self,
        tmp_path: Path,
    ) -> None:
        changes = (
            CodeChange(
                file_path="src/synthorg/meta/strategies/missing.py",
                operation=CodeOperation.MODIFY,
                old_content="old",
                new_content="new",
                description="modify",
                reasoning="r",
            ),
        )
        applier = CodeApplier(
            ci_validator=_mock_ci_validator(),
            github_client=_mock_github_client(),
            code_modification_config=CodeModificationConfig(),
        )
        proposal = _code_proposal(changes=changes)
        with patch(
            "synthorg.meta.appliers.code_applier.Path.cwd",
            return_value=tmp_path,
        ):
            result = await applier.dry_run(proposal)
        assert not result.success
        assert "MODIFY target does not exist" in (result.error_message or "")

    async def test_dry_run_delete_missing_file(
        self,
        tmp_path: Path,
    ) -> None:
        changes = (
            CodeChange(
                file_path="src/synthorg/meta/strategies/gone.py",
                operation=CodeOperation.DELETE,
                old_content="old content",
                description="delete",
                reasoning="r",
            ),
        )
        applier = CodeApplier(
            ci_validator=_mock_ci_validator(),
            github_client=_mock_github_client(),
            code_modification_config=CodeModificationConfig(),
        )
        proposal = _code_proposal(changes=changes)
        with patch(
            "synthorg.meta.appliers.code_applier.Path.cwd",
            return_value=tmp_path,
        ):
            result = await applier.dry_run(proposal)
        assert not result.success
        assert "DELETE target does not exist" in (result.error_message or "")

    async def test_dry_run_create_already_exists(
        self,
        tmp_path: Path,
    ) -> None:
        target = tmp_path / "src" / "synthorg" / "meta" / "strategies"
        target.mkdir(parents=True)
        (target / "new.py").write_text("existing")
        applier = CodeApplier(
            ci_validator=_mock_ci_validator(),
            github_client=_mock_github_client(),
            code_modification_config=CodeModificationConfig(),
        )
        proposal = _code_proposal()
        with patch(
            "synthorg.meta.appliers.code_applier.Path.cwd",
            return_value=tmp_path,
        ):
            result = await applier.dry_run(proposal)
        assert not result.success
        assert "CREATE target already exists" in (result.error_message or "")

    async def test_partial_push_failure_cleans_up_branch(
        self,
        tmp_path: Path,
    ) -> None:
        """Branch is deleted when push fails after some files pushed."""
        ci = _mock_ci_validator(_ci_pass())
        gh = _mock_github_client()
        # Fail on the first push_change call.
        gh.push_change = AsyncMock(
            side_effect=GitHubAPIError(
                status_code=500,
                action="push file",
                body="internal error",
            ),
        )
        applier = CodeApplier(
            ci_validator=ci,
            github_client=gh,
            code_modification_config=CodeModificationConfig(),
        )
        proposal = _code_proposal()

        with patch(
            "synthorg.meta.appliers.code_applier.Path.cwd",
            return_value=tmp_path,
        ):
            strategies_dir = tmp_path / "src" / "synthorg" / "meta" / "strategies"
            strategies_dir.mkdir(parents=True)
            result = await applier.apply(proposal)

        assert not result.success
        # Branch was created then cleaned up after push failure.
        gh.create_branch.assert_awaited_once()
        gh.delete_branch.assert_awaited()


class TestGitHubSanitization:
    """Response body sanitization tests."""

    def test_strips_bearer_token(self) -> None:
        text = "Authorization: Bearer ghp_abc123 denied"
        result = _sanitize_response_body(text)
        assert "ghp_abc123" not in result
        assert "[REDACTED]" in result

    def test_strips_ghp_token(self) -> None:
        text = "Token ghp_abc123XYZ456 is invalid"
        result = _sanitize_response_body(text)
        assert "ghp_abc123XYZ456" not in result
        assert "[REDACTED]" in result

    def test_strips_gho_token(self) -> None:
        text = "Token gho_installation456 expired"
        result = _sanitize_response_body(text)
        assert "gho_installation456" not in result

    def test_strips_github_pat(self) -> None:
        text = "github_pat_abc_def123 unauthorized"
        result = _sanitize_response_body(text)
        assert "github_pat_abc_def123" not in result

    def test_strips_authorization_header(self) -> None:
        text = "Authorization: token secret_value\nnext line"
        result = _sanitize_response_body(text)
        assert "secret_value" not in result

    def test_preserves_safe_content(self) -> None:
        text = '{"message": "Not Found", "status": "404"}'
        result = _sanitize_response_body(text)
        assert result == text


class TestGitHubExceptions:
    """Custom exception type tests."""

    def test_github_api_error_attributes(self) -> None:
        err = GitHubAPIError(status_code=500, action="push", body="oops")
        assert err.status_code == 500
        assert err.action == "push"
        assert err.body == "oops"
        assert "500" in str(err)
        assert "push" in str(err)

    def test_github_auth_error_is_subclass(self) -> None:
        err = GitHubAuthError(status_code=401, action="verify", body="bad")
        assert isinstance(err, GitHubAPIError)


class TestPartialWriteError:
    """Regression tests for the partial-revert path (#1683 round 3).

    Verifies that ``_write_changes`` records the successfully-written
    subset on ``PartialWriteError.applied`` so the outer ``apply()``
    revert clobbers only those files instead of defensively touching
    every change in the proposal.
    """

    def test_write_changes_carries_applied_subset_on_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """First two CREATEs land; the third raises -> applied = (c1, c2)."""
        target_a = tmp_path / "a.py"
        target_b = tmp_path / "b.py"
        target_c = tmp_path / "c.py"
        change_a = CodeChange(
            file_path="a.py",
            operation=CodeOperation.CREATE,
            new_content="A\n",
            description="A",
            reasoning="A",
        )
        change_b = CodeChange(
            file_path="b.py",
            operation=CodeOperation.CREATE,
            new_content="B\n",
            description="B",
            reasoning="B",
        )
        change_c = CodeChange(
            file_path="c.py",
            operation=CodeOperation.CREATE,
            new_content="C\n",
            description="C",
            reasoning="C",
        )
        # Patch ``_apply_single_change`` so c.py's write blows up; the
        # others write to disk normally.
        from synthorg.meta.appliers import code_applier as code_applier_module

        original = code_applier_module._apply_single_change

        def faulty(change: CodeChange, file_path: Path) -> None:
            if change.file_path == "c.py":
                msg = "synthetic write failure"
                raise OSError(msg)
            original(change, file_path)

        monkeypatch.setattr(code_applier_module, "_apply_single_change", faulty)

        with pytest.raises(PartialWriteError) as excinfo:
            CodeApplier._write_changes(
                (change_a, change_b, change_c),
                tmp_path,
            )

        # Only the first two changes should be on the applied tuple --
        # the failure happened on c.py before its write completed.
        assert tuple(c.file_path for c in excinfo.value.applied) == ("a.py", "b.py")
        # Sanity: the production write actually happened for a.py / b.py
        # (proves the failure was synthetic, not a setup bug).
        assert target_a.exists()
        assert target_b.exists()
        assert not target_c.exists()

    def test_partial_write_error_init_preserves_applied(self) -> None:
        """The exception class itself round-trips ``applied``."""
        change = CodeChange(
            file_path="a.py",
            operation=CodeOperation.CREATE,
            new_content="A\n",
            description="A",
            reasoning="A",
        )
        err = PartialWriteError("boom", applied=(change,))
        assert err.applied == (change,)
        assert "boom" in str(err)
