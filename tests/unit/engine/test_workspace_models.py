"""Tests for workspace isolation domain models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.core.enums import ConflictEscalation, ConflictType
from synthorg.engine.workspace.models import (
    MergeConflict,
    MergeResult,
    WorkspaceGroupResult,
    WorkspaceRequest,
)

from .conftest import make_merge_result, make_workspace

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_CREATED_AT = datetime(2026, 3, 8, tzinfo=UTC)


def _make_workspace_request(
    *,
    task_id: str = "task-1",
    agent_id: str = "agent-1",
    base_branch: str = "main",
    file_scope: tuple[str, ...] = (),
) -> WorkspaceRequest:
    return WorkspaceRequest(
        task_id=task_id,
        agent_id=agent_id,
        base_branch=base_branch,
        file_scope=file_scope,
    )


def _make_merge_conflict(
    *,
    file_path: str = "src/main.py",
    conflict_type: ConflictType = ConflictType.TEXTUAL,
    ours_content: str = "ours",
    theirs_content: str = "theirs",
) -> MergeConflict:
    return MergeConflict(
        file_path=file_path,
        conflict_type=conflict_type,
        ours_content=ours_content,
        theirs_content=theirs_content,
    )


# ---------------------------------------------------------------------------
# WorkspaceRequest
# ---------------------------------------------------------------------------


class TestWorkspaceRequest:
    """Tests for WorkspaceRequest model."""

    @pytest.mark.unit
    def test_minimal_request(self) -> None:
        """Required fields only, defaults applied."""
        req = _make_workspace_request()
        assert req.task_id == "task-1"
        assert req.agent_id == "agent-1"
        assert req.base_branch == "main"
        assert req.file_scope == ()

    @pytest.mark.unit
    def test_request_with_file_scope(self) -> None:
        """File scope is preserved."""
        req = _make_workspace_request(
            file_scope=("src/a.py", "src/b.py"),
        )
        assert req.file_scope == ("src/a.py", "src/b.py")

    @pytest.mark.unit
    def test_custom_base_branch(self) -> None:
        """Custom base branch is accepted."""
        req = _make_workspace_request(base_branch="develop")
        assert req.base_branch == "develop"

    @pytest.mark.unit
    def test_frozen(self) -> None:
        """WorkspaceRequest is immutable."""
        req = _make_workspace_request()
        with pytest.raises(ValidationError, match="frozen"):
            req.task_id = "other"  # type: ignore[misc]

    @pytest.mark.unit
    def test_blank_task_id_rejected(self) -> None:
        """Empty task_id is rejected by NotBlankStr."""
        with pytest.raises(ValidationError):
            _make_workspace_request(task_id="")

    @pytest.mark.unit
    def test_whitespace_task_id_rejected(self) -> None:
        """Whitespace-only task_id is rejected."""
        with pytest.raises(ValidationError):
            _make_workspace_request(task_id="   ")

    @pytest.mark.unit
    def test_blank_agent_id_rejected(self) -> None:
        """Empty agent_id is rejected."""
        with pytest.raises(ValidationError):
            _make_workspace_request(agent_id="")

    @pytest.mark.unit
    def test_blank_base_branch_rejected(self) -> None:
        """Empty base_branch is rejected."""
        with pytest.raises(ValidationError):
            _make_workspace_request(base_branch="")


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


class TestWorkspace:
    """Tests for Workspace model."""

    @pytest.mark.unit
    def test_all_fields(self) -> None:
        """All fields are stored correctly."""
        ws = make_workspace(
            worktree_path="worktrees/ws-001",
            branch_name="workspace/task-1/ws-001",
        )
        assert ws.workspace_id == "ws-001"
        assert ws.task_id == "task-1"
        assert ws.agent_id == "agent-1"
        assert ws.branch_name == "workspace/task-1/ws-001"
        assert ws.worktree_path == "worktrees/ws-001"
        assert ws.base_branch == "main"
        assert ws.created_at == _DEFAULT_CREATED_AT
        assert isinstance(ws.created_at, datetime)

    @pytest.mark.unit
    def test_frozen(self) -> None:
        """Workspace is immutable."""
        ws = make_workspace()
        with pytest.raises(ValidationError, match="frozen"):
            ws.workspace_id = "other"  # type: ignore[misc]

    @pytest.mark.unit
    def test_blank_workspace_id_rejected(self) -> None:
        """Empty workspace_id is rejected."""
        with pytest.raises(ValidationError):
            make_workspace(workspace_id="")

    @pytest.mark.unit
    def test_blank_branch_name_rejected(self) -> None:
        """Empty branch_name is rejected."""
        with pytest.raises(ValidationError):
            make_workspace(branch_name="")

    @pytest.mark.unit
    def test_blank_worktree_path_rejected(self) -> None:
        """Empty worktree_path is rejected."""
        with pytest.raises(ValidationError):
            make_workspace(worktree_path="")


# ---------------------------------------------------------------------------
# MergeConflict
# ---------------------------------------------------------------------------


class TestMergeConflict:
    """Tests for MergeConflict model."""

    @pytest.mark.unit
    def test_all_fields(self) -> None:
        """All fields stored correctly."""
        mc = _make_merge_conflict()
        assert mc.file_path == "src/main.py"
        assert mc.conflict_type is ConflictType.TEXTUAL
        assert mc.ours_content == "ours"
        assert mc.theirs_content == "theirs"

    @pytest.mark.unit
    def test_frozen(self) -> None:
        """MergeConflict is immutable."""
        mc = _make_merge_conflict()
        with pytest.raises(ValidationError, match="frozen"):
            mc.file_path = "other.py"  # type: ignore[misc]

    @pytest.mark.unit
    def test_blank_file_path_rejected(self) -> None:
        """Empty file_path is rejected."""
        with pytest.raises(ValidationError):
            _make_merge_conflict(file_path="")

    @pytest.mark.unit
    def test_empty_content_allowed(self) -> None:
        """Empty content strings are valid defaults."""
        mc = MergeConflict(
            file_path="a.py",
            conflict_type=ConflictType.TEXTUAL,
        )
        assert mc.ours_content == ""
        assert mc.theirs_content == ""


# ---------------------------------------------------------------------------
# MergeResult
# ---------------------------------------------------------------------------


class TestMergeResult:
    """Tests for MergeResult model."""

    @pytest.mark.unit
    def test_successful_merge(self) -> None:
        """Successful merge with commit SHA."""
        mr = make_merge_result(success=True, merged_commit_sha="abc123")
        assert mr.success is True
        assert mr.merged_commit_sha == "abc123"
        assert mr.conflicts == ()
        assert mr.escalation is None

    @pytest.mark.unit
    def test_failed_merge_with_conflicts(self) -> None:
        """Failed merge carries conflict details."""
        conflict = _make_merge_conflict()
        mr = make_merge_result(
            success=False,
            conflicts=(conflict,),
            escalation=ConflictEscalation.HUMAN,
            merged_commit_sha=None,
        )
        assert mr.success is False
        assert len(mr.conflicts) == 1
        assert mr.escalation is ConflictEscalation.HUMAN
        assert mr.merged_commit_sha is None

    @pytest.mark.unit
    def test_frozen(self) -> None:
        """MergeResult is immutable."""
        mr = make_merge_result()
        with pytest.raises(ValidationError, match="frozen"):
            mr.success = False  # type: ignore[misc]

    @pytest.mark.unit
    def test_negative_duration_rejected(self) -> None:
        """Negative duration_seconds is rejected."""
        with pytest.raises(ValidationError):
            make_merge_result(duration_seconds=-1.0)

    @pytest.mark.unit
    def test_success_with_conflicts_rejected(self) -> None:
        """Successful merge cannot have conflicts."""
        conflict = _make_merge_conflict()
        with pytest.raises(
            ValidationError,
            match="Successful merge cannot have conflicts",
        ):
            MergeResult(
                workspace_id="ws-001",
                branch_name="workspace/task-1",
                success=True,
                conflicts=(conflict,),
                merged_commit_sha="abc123",
                duration_seconds=0.5,
            )

    @pytest.mark.unit
    def test_success_without_sha_rejected(self) -> None:
        """Successful merge must have a commit SHA."""
        with pytest.raises(
            ValidationError,
            match="Successful merge must have a commit SHA",
        ):
            MergeResult(
                workspace_id="ws-001",
                branch_name="workspace/task-1",
                success=True,
                merged_commit_sha=None,
                duration_seconds=0.5,
            )

    @pytest.mark.unit
    def test_failure_with_sha_rejected(self) -> None:
        """Failed merge cannot have a commit SHA."""
        with pytest.raises(
            ValidationError,
            match="Failed merge cannot have a commit SHA",
        ):
            MergeResult(
                workspace_id="ws-001",
                branch_name="workspace/task-1",
                success=False,
                merged_commit_sha="abc123",
                duration_seconds=0.5,
            )


# ---------------------------------------------------------------------------
# WorkspaceGroupResult
# ---------------------------------------------------------------------------


class TestWorkspaceGroupResult:
    """Tests for WorkspaceGroupResult model."""

    @pytest.mark.unit
    def test_all_merged_true(self) -> None:
        """all_merged is True when all results succeed."""
        mr1 = make_merge_result(workspace_id="ws-1", success=True)
        mr2 = make_merge_result(workspace_id="ws-2", success=True)
        result = WorkspaceGroupResult(
            group_id="grp-1",
            merge_results=(mr1, mr2),
            duration_seconds=3.0,
        )
        assert result.all_merged is True
        assert result.total_conflicts == 0

    @pytest.mark.unit
    def test_all_merged_false_when_any_fails(self) -> None:
        """all_merged is False when any result fails."""
        conflict = _make_merge_conflict()
        mr1 = make_merge_result(workspace_id="ws-1", success=True)
        mr2 = make_merge_result(
            workspace_id="ws-2",
            success=False,
            conflicts=(conflict,),
            merged_commit_sha=None,
        )
        result = WorkspaceGroupResult(
            group_id="grp-1",
            merge_results=(mr1, mr2),
            duration_seconds=3.0,
        )
        assert result.all_merged is False
        assert result.total_conflicts == 1

    @pytest.mark.unit
    def test_all_merged_false_when_empty(self) -> None:
        """all_merged is False when no merge results exist."""
        result = WorkspaceGroupResult(
            group_id="grp-1",
            merge_results=(),
            duration_seconds=0.0,
        )
        assert result.all_merged is False
        assert result.total_conflicts == 0

    @pytest.mark.unit
    def test_total_conflicts_sums_all(self) -> None:
        """total_conflicts sums across all merge results."""
        c1 = _make_merge_conflict(file_path="a.py")
        c2 = _make_merge_conflict(file_path="b.py")
        c3 = _make_merge_conflict(file_path="c.py")
        mr1 = make_merge_result(
            workspace_id="ws-1",
            success=False,
            conflicts=(c1, c2),
            merged_commit_sha=None,
        )
        mr2 = make_merge_result(
            workspace_id="ws-2",
            success=False,
            conflicts=(c3,),
            merged_commit_sha=None,
        )
        result = WorkspaceGroupResult(
            group_id="grp-1",
            merge_results=(mr1, mr2),
            duration_seconds=5.0,
        )
        assert result.total_conflicts == 3

    @pytest.mark.unit
    def test_frozen(self) -> None:
        """WorkspaceGroupResult is immutable."""
        result = WorkspaceGroupResult(
            group_id="grp-1",
            duration_seconds=0.0,
        )
        with pytest.raises(ValidationError, match="frozen"):
            result.group_id = "other"  # type: ignore[misc]

    @pytest.mark.unit
    def test_negative_duration_rejected(self) -> None:
        """Negative duration_seconds is rejected."""
        with pytest.raises(ValidationError):
            WorkspaceGroupResult(
                group_id="grp-1",
                duration_seconds=-1.0,
            )
