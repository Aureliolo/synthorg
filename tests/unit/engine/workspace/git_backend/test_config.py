"""Unit tests for ``GitBackendConfig`` discriminator + validation."""

import pytest
from pydantic import ValidationError

from synthorg.core.enums import GitBackendType
from synthorg.engine.workspace.git_backend import GitBackendConfig

pytestmark = pytest.mark.unit


class TestGitBackendConfig:
    def test_default_is_embedded(self) -> None:
        cfg = GitBackendConfig()
        assert cfg.kind is GitBackendType.EMBEDDED
        assert cfg.embedded_subdir == "git-repos"

    def test_is_frozen(self) -> None:
        cfg = GitBackendConfig()
        with pytest.raises(ValidationError):
            cfg.kind = GitBackendType.LOCAL_PATH  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            GitBackendConfig(unexpected=1)  # type: ignore[call-arg]

    def test_local_path_requires_path(self) -> None:
        with pytest.raises(ValidationError, match="local_repo_path"):
            GitBackendConfig(kind=GitBackendType.LOCAL_PATH)

    def test_local_path_accepts_path(self) -> None:
        cfg = GitBackendConfig(
            kind=GitBackendType.LOCAL_PATH,
            local_repo_path="/srv/repo",
        )
        assert cfg.local_repo_path == "/srv/repo"

    def test_external_remote_requires_connection_name(self) -> None:
        with pytest.raises(ValidationError, match="remote_connection_name"):
            GitBackendConfig(kind=GitBackendType.EXTERNAL_REMOTE)

    def test_external_remote_accepts_connection_name(self) -> None:
        cfg = GitBackendConfig(
            kind=GitBackendType.EXTERNAL_REMOTE,
            remote_connection_name="github-main",
        )
        assert cfg.remote_connection_name == "github-main"

    def test_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            GitBackendConfig(git_cmd_timeout_seconds=0.0)
