"""Per-execution per-project Docker sandbox mount + owner-key prefix.

Unit-level coverage of the structural pieces (mount-root resolution,
host-config bind, lifecycle owner-key project prefix, correlation
context). The live container isolation guarantee (project-A cannot see
project-B files) is exercised by the Docker-gated integration test.
"""

from pathlib import Path
from typing import Any, cast

import pytest
import structlog.contextvars

from synthorg.core.types import NotBlankStr
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox, _to_posix_bind_path
from synthorg.tools.sandbox.errors import SandboxError
from synthorg.tools.sandbox.lifecycle.protocol import SandboxLifecycleStrategy
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def _sandbox(workspace: Path) -> DockerSandbox:
    return DockerSandbox(workspace=workspace)


def _make_project(workspace: Path, project_id: str) -> Path:
    root = workspace / "projects" / project_id
    root.mkdir(parents=True, exist_ok=True)
    return root


class TestProjectRoot:
    async def test_none_returns_workspace_root(self, tmp_path: Path) -> None:
        sandbox = _sandbox(tmp_path)
        assert await sandbox._project_root(None) == tmp_path

    async def test_project_id_returns_subtree(self, tmp_path: Path) -> None:
        _make_project(tmp_path, "proj-a")
        sandbox = _sandbox(tmp_path)
        assert await sandbox._project_root("proj-a") == (
            tmp_path / "projects" / "proj-a"
        )

    @pytest.mark.parametrize("bad", ["../escape", "a/b", "a\\b", "..", ".", "", "   "])
    async def test_traversal_rejected(self, tmp_path: Path, bad: str) -> None:
        sandbox = _sandbox(tmp_path)
        with pytest.raises(SandboxError, match="path-separator"):
            await sandbox._project_root(bad)

    async def test_missing_project_dir_rejected(self, tmp_path: Path) -> None:
        sandbox = _sandbox(tmp_path)
        with pytest.raises(SandboxError, match="does not exist"):
            await sandbox._project_root("never-provisioned")

    async def test_oversized_project_id_rejected(self, tmp_path: Path) -> None:
        # An oversized project_id makes resolve()/is_dir() raise OSError on
        # most filesystems; either way it must surface as SandboxError,
        # never a leaked OSError.
        sandbox = _sandbox(tmp_path)
        with pytest.raises(SandboxError):
            await sandbox._project_root("a" * 5000)

    async def test_symlinked_project_escaping_root_rejected(
        self, tmp_path: Path
    ) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        projects = tmp_path / "projects"
        projects.mkdir()
        try:
            (projects / "evil").symlink_to(outside, target_is_directory=True)
        except OSError, NotImplementedError:
            pytest.skip("symlink creation not permitted on this platform")
        sandbox = _sandbox(tmp_path)
        with pytest.raises(SandboxError, match="escapes projects root"):
            await sandbox._project_root("evil")


class TestHostConfigBindsProjectSubtree:
    def test_bind_targets_project_subtree(self, tmp_path: Path) -> None:
        project_root = _make_project(tmp_path, "proj-a")
        sandbox = _sandbox(tmp_path)
        host_config = sandbox._build_host_config(project_root)
        bind = cast("dict[str, Any]", host_config)["Binds"][0]
        assert bind.startswith(f"{_to_posix_bind_path(project_root)}:")
        assert bind.endswith(":/workspace:ro") or ":/workspace:" in bind

    def test_default_bind_is_whole_workspace(self, tmp_path: Path) -> None:
        sandbox = _sandbox(tmp_path)
        host_config = sandbox._build_host_config()
        bind = cast("dict[str, Any]", host_config)["Binds"][0]
        assert bind.startswith(f"{_to_posix_bind_path(tmp_path)}:")


class TestCwdResolutionUnderProjectRoot:
    def test_cwd_relative_to_effective_root(self, tmp_path: Path) -> None:
        project_root = _make_project(tmp_path, "proj-a")
        sub = project_root / "src"
        sub.mkdir()
        sandbox = _sandbox(tmp_path)
        container_cwd = sandbox._resolve_cwd_in_container(sub, project_root)
        assert container_cwd == "/workspace/src"

    def test_cwd_outside_effective_root_rejected(self, tmp_path: Path) -> None:
        project_a = _make_project(tmp_path, "proj-a")
        project_b = _make_project(tmp_path, "proj-b")
        sandbox = _sandbox(tmp_path)
        with pytest.raises(SandboxError, match="outside workspace"):
            sandbox._validate_cwd(project_b, project_a)


class TestOwnerKeyProjectPrefix:
    def test_explicit_owner_is_project_prefixed(self, tmp_path: Path) -> None:
        sandbox = _sandbox(tmp_path)
        key, _owns = sandbox._resolve_lifecycle(
            "agent-1",
            project_id="proj-a",
        )
        assert key == "proj-a:agent-1"

    def test_no_project_leaves_key_unprefixed(self, tmp_path: Path) -> None:
        sandbox = _sandbox(tmp_path)
        key, _owns = sandbox._resolve_lifecycle("agent-1", project_id=None)
        assert key == "agent-1"

    def test_different_projects_yield_distinct_keys(self, tmp_path: Path) -> None:
        sandbox = _sandbox(tmp_path)
        key_a, _ = sandbox._resolve_lifecycle("agent-1", project_id="proj-a")
        key_b, _ = sandbox._resolve_lifecycle("agent-1", project_id="proj-b")
        assert key_a != key_b

    def test_invalid_project_prefix_degrades_to_ephemeral(self, tmp_path: Path) -> None:
        # A valid owner combined with a project_id that produces an
        # out-of-format prefixed key must not poison the lifecycle key;
        # it degrades to an ephemeral per-call key instead.
        sandbox = _sandbox(tmp_path)
        key, owns = sandbox._resolve_lifecycle("agent-1", project_id="bad@id")
        assert key.startswith("per-call:")
        assert owns is False


class TestContextProject:
    def test_reads_project_id_from_correlation_context(self, tmp_path: Path) -> None:
        sandbox = _sandbox(tmp_path)
        structlog.contextvars.bind_contextvars(project_id="ctx-proj")
        try:
            assert sandbox._context_project() == "ctx-proj"
        finally:
            structlog.contextvars.unbind_contextvars("project_id")

    def test_absent_context_returns_none(self, tmp_path: Path) -> None:
        structlog.contextvars.clear_contextvars()
        sandbox = _sandbox(tmp_path)
        assert sandbox._context_project() is None


class TestReleaseOwnerProjectPrefix:
    async def test_release_uses_project_prefixed_key(self, tmp_path: Path) -> None:
        # release_owner fires AFTER the correlation scope exits, so the
        # project must be passed explicitly; the released key must match
        # the project-prefixed key execute() acquired the container under.
        strategy = mock_of[SandboxLifecycleStrategy]()
        sandbox = DockerSandbox(workspace=tmp_path, lifecycle_strategy=strategy)

        await sandbox.release_owner(
            NotBlankStr("agent-1"),
            project_id=NotBlankStr("proj-a"),
        )

        strategy.release.assert_awaited_once()
        assert strategy.release.await_args.kwargs["owner_id"] == "proj-a:agent-1"

    async def test_release_without_project_is_unprefixed(self, tmp_path: Path) -> None:
        strategy = mock_of[SandboxLifecycleStrategy]()
        sandbox = DockerSandbox(workspace=tmp_path, lifecycle_strategy=strategy)

        await sandbox.release_owner(NotBlankStr("agent-1"))

        assert strategy.release.await_args.kwargs["owner_id"] == "agent-1"
