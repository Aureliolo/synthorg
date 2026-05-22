"""Unit tests for the EnvironmentService orchestration."""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests._shared import FakeClock

from synthorg.core.enums import EnvironmentType
from synthorg.core.project_environment import ProjectEnvironment
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import EnvironmentConfigError, EnvironmentProvisionError
from synthorg.engine.workspace.environment.config import EnvironmentConfig
from synthorg.engine.workspace.environment.manifest import ManifestEnvironmentStrategy
from synthorg.engine.workspace.environment.protocol import (
    CommandOutcome,
    ProvisionedEnvironment,
)
from synthorg.engine.workspace.environment.service import EnvironmentService

pytestmark = pytest.mark.unit

_MANIFEST = "synthorg.env.yaml"
_PROJECT = NotBlankStr("proj-1")
_SUBPROCESS = NotBlankStr("subprocess")


class _InMemoryRepo:
    def __init__(self) -> None:
        self.rows: dict[str, ProjectEnvironment] = {}
        self.get_calls = 0
        self.save_calls = 0

    async def save(self, entity: ProjectEnvironment) -> None:
        self.save_calls += 1
        self.rows[entity.project_id] = entity

    async def get(self, entity_id: NotBlankStr) -> ProjectEnvironment | None:
        self.get_calls += 1
        return self.rows.get(entity_id)

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[ProjectEnvironment, ...]:
        rows = sorted(self.rows.values(), key=lambda r: r.project_id)
        return tuple(rows[offset : offset + limit])

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self.rows.pop(entity_id, None) is not None


class _Runner:
    def __init__(self, *, exit_code: int = 0) -> None:
        self.calls = 0
        self._exit_code = exit_code

    async def run(
        self,
        *,
        command: str,
        args: tuple[str, ...],
        cwd: Path,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 -- matches runner protocol
    ) -> CommandOutcome:
        del command, cwd, env, timeout
        self.calls += 1
        return CommandOutcome(command=args[-1], exit_code=self._exit_code)


class _Committer:
    def __init__(self) -> None:
        self.commits: list[tuple[str, ...]] = []

    async def commit(
        self, *, workspace_path: Path, paths: tuple[str, ...], message: str
    ) -> bool:
        del workspace_path, message
        self.commits.append(paths)
        return bool(paths)


def _strategy() -> ManifestEnvironmentStrategy:
    return ManifestEnvironmentStrategy(
        manifest_filename=_MANIFEST,
        provision_timeout_seconds=60.0,
        clock=FakeClock(),
    )


def _service(
    repo: _InMemoryRepo,
    *,
    committer: _Committer | None = None,
    config: EnvironmentConfig | None = None,
) -> EnvironmentService:
    return EnvironmentService(
        repo=repo,
        strategy=_strategy(),
        config=config if config is not None else EnvironmentConfig(),
        committer=committer,
        clock=FakeClock(),
    )


def _write_manifest(workspace: Path, setup: str = '["echo hi"]') -> None:
    (workspace / _MANIFEST).write_text(
        f'language: python\ntest_command: "pytest"\nsetup_commands: {setup}\n',
        encoding="utf-8",
    )


async def _provision(
    service: EnvironmentService, workspace: Path, runner: _Runner
) -> ProvisionedEnvironment:
    return await service.get_or_provision(
        _PROJECT, workspace_path=workspace, runner=runner, sandbox_kind=_SUBPROCESS
    )


class TestEnvironmentService:
    async def test_first_provision_persists_and_commits(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path)
        repo, committer, runner = _InMemoryRepo(), _Committer(), _Runner()

        env = await _provision(_service(repo, committer=committer), tmp_path, runner)

        assert env.environment_type is EnvironmentType.MANIFEST
        assert repo.rows["proj-1"].project_id == "proj-1"
        assert runner.calls == 1
        assert repo.save_calls == 1
        # Committed the manifest + generated bootstrap.sh.
        assert committer.commits
        assert "synthorg.env.yaml" in committer.commits[0]
        assert "bootstrap.sh" in committer.commits[0]

    async def test_reuse_on_unchanged_declaration(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path)
        repo, runner = _InMemoryRepo(), _Runner()
        service = _service(repo)

        first = await _provision(service, tmp_path, runner)
        second = await _provision(service, tmp_path, runner)

        assert first.declaration_hash == second.declaration_hash
        assert first.environment_type == second.environment_type
        # No re-provision: setup ran only once.
        assert runner.calls == 1
        assert repo.save_calls == 1

    async def test_persisted_reuse_after_fresh_service(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path)
        repo, runner = _InMemoryRepo(), _Runner()
        await _provision(_service(repo), tmp_path, runner)

        # New service (empty memo) reuses the persisted row, no re-provision.
        runner2 = _Runner()
        env = await _provision(_service(repo), tmp_path, runner2)
        assert env.declaration_hash
        assert runner2.calls == 0

    async def test_declaration_change_reprovisions(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, setup='["echo a"]')
        repo, runner = _InMemoryRepo(), _Runner()
        service = _service(repo)
        await _provision(service, tmp_path, runner)

        _write_manifest(tmp_path, setup='["echo b"]')
        await _provision(service, tmp_path, runner)
        assert runner.calls == 2
        assert repo.save_calls == 2

    async def test_kind_change_reprovisions(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path)
        repo, runner = _InMemoryRepo(), _Runner()
        # Prior provisioning precedes the FakeClock "now" (2026-01-01) so
        # the preserved provisioned_at stays <= the re-provision updated_at.
        ts = datetime(2025, 12, 1, tzinfo=UTC)
        repo.rows["proj-1"] = ProjectEnvironment(
            project_id=_PROJECT,
            environment_type=EnvironmentType.NIX,
            declaration_hash=NotBlankStr("f" * 64),
            provisioned_at=ts,
            updated_at=ts,
        )

        env = await _provision(_service(repo), tmp_path, runner)
        assert env.environment_type is EnvironmentType.MANIFEST
        # First-provision timestamp is preserved across the kind switch.
        assert repo.rows["proj-1"].provisioned_at == ts

    async def test_provision_failure_is_fail_loud(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path)
        repo = _InMemoryRepo()

        with pytest.raises(EnvironmentProvisionError):
            await _provision(_service(repo), tmp_path, _Runner(exit_code=1))
        # No row persisted on failure.
        assert repo.save_calls == 0

    async def test_no_declaration_without_auto_seed_raises(
        self, tmp_path: Path
    ) -> None:
        repo = _InMemoryRepo()
        service = _service(repo, config=EnvironmentConfig(auto_seed=False))

        with pytest.raises(EnvironmentConfigError):
            await _provision(service, tmp_path, _Runner())

    async def test_env_vars_returned_on_fresh_and_reuse(self, tmp_path: Path) -> None:
        (tmp_path / _MANIFEST).write_text(
            'language: python\ntest_command: "pytest"\nsetup_commands: []\n'
            'env: {"VIRTUAL_ENV": "/w/.venv"}\n',
            encoding="utf-8",
        )
        repo, runner = _InMemoryRepo(), _Runner()
        service = _service(repo)

        fresh = await _provision(service, tmp_path, runner)
        assert fresh.env_vars == {"VIRTUAL_ENV": "/w/.venv"}

        # Reuse path re-derives env_vars from the declaration (not persisted).
        reused = await _provision(service, tmp_path, runner)
        assert reused.env_vars == {"VIRTUAL_ENV": "/w/.venv"}

    async def test_auto_seed_then_provision_empty_setup(self, tmp_path: Path) -> None:
        # No manifest written; auto_seed scaffolds the default (empty setup).
        repo, runner = _InMemoryRepo(), _Runner()
        env = await _provision(_service(repo), tmp_path, runner)
        assert (tmp_path / _MANIFEST).is_file()
        assert env.environment_type is EnvironmentType.MANIFEST
        assert runner.calls == 0  # default scaffold has no setup commands

    async def test_concurrent_provision_same_project_provisions_once(
        self, tmp_path: Path
    ) -> None:
        # The per-project lock serialises concurrent first-touch: one task
        # provisions, the rest reuse. Exactly one setup run + one save.
        _write_manifest(tmp_path)
        repo, runner = _InMemoryRepo(), _Runner()
        service = _service(repo)

        results = await asyncio.gather(
            *(_provision(service, tmp_path, runner) for _ in range(5))
        )

        assert repo.save_calls == 1
        assert runner.calls == 1  # the single "echo hi" setup command
        hashes = {r.declaration_hash for r in results}
        assert len(hashes) == 1  # every caller got the same environment

    async def test_cancellation_mid_provision_persists_nothing(
        self, tmp_path: Path
    ) -> None:
        # A cancellation during the setup run must not persist a partial
        # row or poison the cache; a later provision succeeds cleanly.
        _write_manifest(tmp_path)
        repo = _InMemoryRepo()

        class _CancellingRunner(_Runner):
            async def run(
                self,
                *,
                command: str,
                args: tuple[str, ...],
                cwd: Path,
                env: Mapping[str, str] | None = None,
                timeout: float | None = None,  # noqa: ASYNC109 -- matches protocol
            ) -> CommandOutcome:
                del command, args, cwd, env, timeout
                raise asyncio.CancelledError

        service = _service(repo)
        with pytest.raises(asyncio.CancelledError):
            await _provision(service, tmp_path, _CancellingRunner())
        assert repo.save_calls == 0
        assert repo.rows == {}

        # The lock was released and nothing was cached: a clean retry works.
        env = await _provision(service, tmp_path, _Runner())
        assert env.environment_type is EnvironmentType.MANIFEST
        assert repo.save_calls == 1
