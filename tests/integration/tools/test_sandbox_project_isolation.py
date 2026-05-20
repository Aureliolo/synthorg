"""Live-container per-project sandbox isolation.

Proves the per-execution per-project mount at the container level: a
project-A execution sees project-A's files at ``/workspace`` and CANNOT
see project-B's files. This is the runtime guarantee the structural
unit test (``tests/unit/tools/sandbox/test_project_mount.py``) cannot
establish on its own.

Requires a running Docker daemon + the test image; skipped otherwise.
"""

import asyncio
from typing import TYPE_CHECKING

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]

_TEST_IMAGE = "python:3.12-slim"


def _docker_and_image_available() -> bool:
    """Check if Docker daemon is reachable and test image exists."""
    try:
        import aiodocker

        async def _check() -> bool:
            client = None
            try:
                client = aiodocker.Docker()
                await client.version()
                await client.images.inspect(_TEST_IMAGE)
            except Exception:
                return False
            else:
                return True
            finally:
                if client is not None:
                    await client.close()

        return asyncio.run(_check())
    except Exception:
        return False


skip_no_docker = pytest.mark.skipif(
    not _docker_and_image_available(),
    reason=f"Docker daemon not available or {_TEST_IMAGE} not pulled",
)


def _seed_project(workspace: Path, project_id: str, marker: str) -> None:
    root = workspace / "projects" / project_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "secret.txt").write_text(marker, encoding="utf-8")


@skip_no_docker
class TestProjectMountIsolation:
    async def test_project_a_cannot_see_project_b(self, tmp_path: Path) -> None:
        _seed_project(tmp_path, "proj-a", "SECRET-A")
        _seed_project(tmp_path, "proj-b", "SECRET-B")
        sandbox = DockerSandbox(
            config=DockerSandboxConfig(image=_TEST_IMAGE, timeout_seconds=60),
            workspace=tmp_path,
        )
        try:
            # Project A sees its own secret at the mount root.
            own = await sandbox.execute(
                command="cat",
                args=("/workspace/secret.txt",),
                project_id=NotBlankStr("proj-a"),
            )
            assert own.success
            assert "SECRET-A" in own.stdout

            # Project A's mount is the project subtree; project B's files
            # are simply not present under /workspace.
            listing = await sandbox.execute(
                command="ls",
                args=("/workspace",),
                project_id=NotBlankStr("proj-a"),
            )
            assert listing.success
            assert "secret.txt" in listing.stdout

            cross = await sandbox.execute(
                command="cat",
                args=("/workspace/../proj-b/secret.txt",),
                project_id=NotBlankStr("proj-a"),
            )
            # The bind mount is rooted at proj-a; proj-b is outside it
            # and unreadable from inside the container.
            assert not cross.success or "SECRET-B" not in cross.stdout
        finally:
            await sandbox.cleanup()

    async def test_no_project_mounts_whole_workspace(self, tmp_path: Path) -> None:
        _seed_project(tmp_path, "proj-a", "SECRET-A")
        sandbox = DockerSandbox(
            config=DockerSandboxConfig(image=_TEST_IMAGE, timeout_seconds=60),
            workspace=tmp_path,
        )
        try:
            # With no project_id the whole workspace root is mounted, so
            # the projects/ tree is visible (the empty-company path).
            listing = await sandbox.execute(
                command="ls",
                args=("/workspace/projects",),
            )
            assert listing.success
            assert "proj-a" in listing.stdout
        finally:
            await sandbox.cleanup()
