"""Acceptance tests for the reproducible per-project environment (#1994).

Validates the locked acceptance criterion under the simulation harness:
a deliverable repo, freshly cloned into a clean environment from its
declared env, builds and passes its tests with no manual setup.

The headline test provisions a tiny deliverable project's declaration
into a real git-backed workspace, commits it, ``git clone``s the repo
into a clean directory (so only committed content travels), runs the
emitted ``bootstrap.sh`` with stock tools (no SynthOrg present), and
asserts the deliverable's own ``test_command`` passes.
"""

import asyncio
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest
from tests._shared import FakeClock

from synthorg.core.enums import GitBackendType
from synthorg.core.project_environment import ProjectEnvironment
from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.environment.committer import GitWorkspaceCommitter
from synthorg.engine.workspace.environment.config import (
    EnvironmentConfig,
    EnvironmentDeps,
)
from synthorg.engine.workspace.environment.factory import build_environment_strategy
from synthorg.engine.workspace.environment.protocol import CommandOutcome
from synthorg.engine.workspace.environment.service import EnvironmentService
from synthorg.engine.workspace.git_backend import (
    GitBackendConfig,
    GitBackendDeps,
    build_git_backend,
)
from synthorg.engine.workspace.project_workspace_service import (
    ProjectWorkspaceService,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("sh") is None or shutil.which("git") is None,
        reason="acceptance test requires POSIX sh + git",
    ),
]


class _InMemoryWorkspaceRepo:
    def __init__(self) -> None:
        self._rows: dict[str, object] = {}

    async def save(self, entity: object) -> None:
        self._rows[entity.project_id] = entity  # type: ignore[attr-defined]

    async def get(self, entity_id: NotBlankStr) -> object | None:
        return self._rows.get(entity_id)

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[object, ...]:
        rows = sorted(self._rows.values(), key=lambda r: r.project_id)  # type: ignore[attr-defined]
        return tuple(rows[offset : offset + limit])

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self._rows.pop(entity_id, None) is not None


class _InMemoryEnvironmentRepo:
    def __init__(self) -> None:
        self.rows: dict[str, ProjectEnvironment] = {}

    async def save(self, entity: ProjectEnvironment) -> None:
        self.rows[entity.project_id] = entity

    async def get(self, entity_id: NotBlankStr) -> ProjectEnvironment | None:
        return self.rows.get(entity_id)

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[ProjectEnvironment, ...]:
        rows = sorted(self.rows.values(), key=lambda r: r.project_id)
        return tuple(rows[offset : offset + limit])

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self.rows.pop(entity_id, None) is not None


class _HostRunner:
    """Runs setup commands as real host subprocesses in the workspace."""

    async def run(
        self,
        *,
        command: str,
        args: tuple[str, ...],
        cwd: Path,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 -- matches runner protocol
    ) -> CommandOutcome:
        del env, timeout
        proc = await asyncio.create_subprocess_exec(
            command,
            *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        return CommandOutcome(
            command=command,
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=out.decode("utf-8", errors="replace"),
            stderr=err.decode("utf-8", errors="replace"),
        )


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- args from test code, not untrusted input
        list(args),
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
    )


async def _provision_workspace(base_root: Path) -> Path:
    config = GitBackendConfig(kind=GitBackendType.EMBEDDED)
    backend = build_git_backend(
        config,
        GitBackendDeps(workspace_base_root=base_root, clock=FakeClock()),
    )
    svc = ProjectWorkspaceService(
        base_root=base_root,
        repo=_InMemoryWorkspaceRepo(),  # type: ignore[arg-type]
        git_backend=backend,
        config=config,
        clock=FakeClock(),
    )
    ws = await svc.get_or_provision(NotBlankStr("proj-1"))
    return Path(ws.workspace_path)


def _env_service(repo: _InMemoryEnvironmentRepo) -> EnvironmentService:
    config = EnvironmentConfig()
    return EnvironmentService(
        repo=repo,
        strategy=build_environment_strategy(config, EnvironmentDeps(clock=FakeClock())),
        config=config,
        committer=GitWorkspaceCommitter(),
        clock=FakeClock(),
    )


_DELIVERABLE_MANIFEST = (
    "language: python\n"
    'setup_commands: ["python -m venv .venv"]\n'
    'test_command: "python -m unittest discover -s tests"\n'
)
_HELLO_PY = "def hello() -> str:\n    return 'hello'\n"
_TEST_PY = (
    "import unittest\n"
    "from hello import hello\n\n\n"
    "class HelloTest(unittest.TestCase):\n"
    "    def test_hello(self) -> None:\n"
    "        self.assertEqual(hello(), 'hello')\n"
)


def _plant_deliverable(workspace: Path) -> None:
    (workspace / "synthorg.env.yaml").write_text(
        _DELIVERABLE_MANIFEST, encoding="utf-8"
    )
    (workspace / "hello.py").write_text(_HELLO_PY, encoding="utf-8")
    tests_dir = workspace / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test_hello.py").write_text(_TEST_PY, encoding="utf-8")
    # Commit the deliverable files (the agent's work) so the clone has them.
    _run("git", "add", "-A", cwd=workspace)
    _run("git", "commit", "-m", "Deliverable project", cwd=workspace)


class TestFreshCloneReproducibility:
    """Acceptance: a fresh clone builds and tests pass with no manual setup."""

    async def test_clone_bootstrap_and_tests_pass(self, tmp_path: Path) -> None:
        base_root = tmp_path / "base"
        workspace = await _provision_workspace(base_root)
        _plant_deliverable(workspace)

        # Provision the declaration: runs setup, emits + commits bootstrap.sh.
        provisioned = await _env_service(_InMemoryEnvironmentRepo()).get_or_provision(
            NotBlankStr("proj-1"),
            workspace_path=workspace,
            runner=_HostRunner(),
            sandbox_kind=NotBlankStr("subprocess"),
        )
        assert provisioned.image_ref is None  # bootstrap path, no image

        # Simulate a fresh clone: only committed content travels.
        clone = tmp_path / "clone"
        clone_result = _run("git", "clone", str(workspace), str(clone), cwd=tmp_path)
        assert clone_result.returncode == 0, clone_result.stderr
        assert (clone / "bootstrap.sh").is_file()
        assert (clone / "synthorg.env.yaml").is_file()

        # Run the emitted bootstrap with stock tools (no SynthOrg present).
        bootstrap = _run("sh", "bootstrap.sh", cwd=clone)
        assert bootstrap.returncode == 0, bootstrap.stderr
        # Setup actually ran in the clone (the declared venv was created).
        assert (clone / ".venv").is_dir()

        # The deliverable's own tests pass in the freshly bootstrapped clone.
        tests = _run("python", "-m", "unittest", "discover", "-s", "tests", cwd=clone)
        assert tests.returncode == 0, tests.stderr


class TestAutoSeedCommit:
    """Acceptance: a fresh project auto-seeds + commits a declaration."""

    async def test_scaffold_is_committed(self, tmp_path: Path) -> None:
        base_root = tmp_path / "base"
        workspace = await _provision_workspace(base_root)

        before = _run(
            "git", "rev-list", "--count", "HEAD", cwd=workspace
        ).stdout.strip()

        await _env_service(_InMemoryEnvironmentRepo()).get_or_provision(
            NotBlankStr("proj-1"),
            workspace_path=workspace,
            runner=_HostRunner(),
            sandbox_kind=NotBlankStr("subprocess"),
        )

        # The default declaration was seeded and committed.
        assert (workspace / "synthorg.env.yaml").is_file()
        assert (workspace / "bootstrap.sh").is_file()
        after = _run("git", "rev-list", "--count", "HEAD", cwd=workspace).stdout.strip()
        assert int(after) == int(before) + 1
        tracked = _run("git", "ls-files", cwd=workspace).stdout
        assert "synthorg.env.yaml" in tracked
        assert "bootstrap.sh" in tracked
