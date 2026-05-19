"""Unit tests for ``build_git_backend`` factory dispatch + fail-fast."""

from pathlib import Path

import pytest

from synthorg.core.enums import GitBackendType
from synthorg.engine.errors import GitBackendConfigError
from synthorg.engine.workspace.git_backend import (
    EmbeddedGitBackend,
    GitBackendConfig,
    GitBackendDeps,
    LocalPathGitBackend,
    build_git_backend,
)

pytestmark = pytest.mark.unit


class TestGitBackendFactory:
    def test_embedded_built_with_base_root(self, tmp_path: Path) -> None:
        backend = build_git_backend(
            GitBackendConfig(),
            GitBackendDeps(workspace_base_root=tmp_path),
        )
        assert isinstance(backend, EmbeddedGitBackend)
        assert backend.get_backend_type() is GitBackendType.EMBEDDED

    def test_embedded_without_base_root_fails_fast(self) -> None:
        with pytest.raises(GitBackendConfigError, match="workspace_base_root"):
            build_git_backend(GitBackendConfig(), GitBackendDeps())

    def test_local_path_built(self, tmp_path: Path) -> None:
        backend = build_git_backend(
            GitBackendConfig(
                kind=GitBackendType.LOCAL_PATH,
                local_repo_path=str(tmp_path / "repo"),
            ),
            GitBackendDeps(),
        )
        assert isinstance(backend, LocalPathGitBackend)

    def test_external_remote_without_catalog_fails_fast(self) -> None:
        with pytest.raises(GitBackendConfigError, match="connection_catalog"):
            build_git_backend(
                GitBackendConfig(
                    kind=GitBackendType.EXTERNAL_REMOTE,
                    remote_connection_name="github-main",
                ),
                GitBackendDeps(),
            )

    def test_registry_covers_every_kind(self) -> None:
        for kind in GitBackendType:
            assert kind in {
                GitBackendType.EMBEDDED,
                GitBackendType.LOCAL_PATH,
                GitBackendType.EXTERNAL_REMOTE,
            }
