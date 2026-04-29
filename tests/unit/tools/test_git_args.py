"""Tests for typed git tool argument models."""

import pytest
from pydantic import ValidationError

from synthorg.tools._git_args import (
    GitBranchArgs,
    GitCloneArgs,
    GitCommitArgs,
    GitDiffArgs,
    GitLogArgs,
    GitStatusArgs,
)


class TestGitStatusArgs:
    @pytest.mark.unit
    def test_default(self) -> None:
        args = GitStatusArgs()
        assert args.short is False
        assert args.porcelain is False


class TestGitLogArgs:
    @pytest.mark.unit
    def test_default(self) -> None:
        args = GitLogArgs()
        assert args.max_count == 10
        assert args.paths == ()

    @pytest.mark.unit
    def test_max_count_bounds(self) -> None:
        GitLogArgs(max_count=1)
        GitLogArgs(max_count=100)
        with pytest.raises(ValidationError):
            GitLogArgs(max_count=0)
        with pytest.raises(ValidationError):
            GitLogArgs(max_count=101)

    @pytest.mark.unit
    def test_paths_must_be_non_blank(self) -> None:
        GitLogArgs(paths=("src/main.py",))
        with pytest.raises(ValidationError):
            GitLogArgs(paths=("",))


class TestGitDiffArgs:
    @pytest.mark.unit
    def test_default(self) -> None:
        args = GitDiffArgs()
        assert args.staged is False
        assert args.stat is False
        assert args.ref1 is None
        assert args.ref2 is None


class TestGitBranchArgs:
    @pytest.mark.unit
    def test_default_action_is_list(self) -> None:
        args = GitBranchArgs()
        assert args.action == "list"

    @pytest.mark.unit
    def test_action_is_closed_literal(self) -> None:
        for action in ("list", "create", "switch", "delete"):
            args = GitBranchArgs.model_validate({"action": action})
            assert args.action == action
        with pytest.raises(ValidationError):
            GitBranchArgs.model_validate({"action": "rename"})


class TestGitCommitArgs:
    @pytest.mark.unit
    def test_construction(self) -> None:
        args = GitCommitArgs(message="initial commit")
        assert args.all is False
        assert args.paths == ()

    @pytest.mark.unit
    def test_blank_message_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GitCommitArgs(message="   ")

    @pytest.mark.unit
    def test_missing_message_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GitCommitArgs.model_validate({})


class TestGitCloneArgs:
    @pytest.mark.unit
    def test_construction(self) -> None:
        args = GitCloneArgs(url="https://github.com/example/repo.git")
        assert args.depth is None

    @pytest.mark.unit
    def test_depth_must_be_positive(self) -> None:
        GitCloneArgs(url="https://x", depth=1)
        with pytest.raises(ValidationError):
            GitCloneArgs(url="https://x", depth=0)
        with pytest.raises(ValidationError):
            GitCloneArgs(url="https://x", depth=-1)

    @pytest.mark.unit
    def test_blank_url_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GitCloneArgs(url="   ")
