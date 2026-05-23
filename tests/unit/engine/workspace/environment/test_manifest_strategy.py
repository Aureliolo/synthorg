"""Unit tests for the bootstrap-manifest environment strategy."""

from collections.abc import Mapping
from pathlib import Path

import pytest

from synthorg.core.enums import EnvironmentType
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import EnvironmentConfigError, EnvironmentProvisionError
from synthorg.engine.workspace.environment.manifest import (
    BOOTSTRAP_SCRIPT_NAME,
    ManifestEnvironmentStrategy,
)
from synthorg.engine.workspace.environment.protocol import (
    CommandOutcome,
    EnvironmentStrategy,
)
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_MANIFEST = "synthorg.env.yaml"


class _RecordingRunner:
    """Fake command runner recording calls; configurable per-command outcome."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.calls: list[tuple[str, tuple[str, ...], Path]] = []
        self._fail_on = fail_on

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
        self.calls.append((command, args, cwd))
        shell_cmd = args[-1] if args else ""
        failed = self._fail_on is not None and self._fail_on in shell_cmd
        return CommandOutcome(
            command=shell_cmd,
            exit_code=1 if failed else 0,
            stdout="out",
            stderr="err" if failed else "",
        )


def _strategy() -> ManifestEnvironmentStrategy:
    return ManifestEnvironmentStrategy(
        manifest_filename=_MANIFEST,
        provision_timeout_seconds=60.0,
        clock=FakeClock(),
    )


def _write_manifest(workspace: Path, body: str) -> None:
    (workspace / _MANIFEST).write_text(body, encoding="utf-8")


class TestProtocolConformance:
    def test_satisfies_protocol(self) -> None:
        assert isinstance(_strategy(), EnvironmentStrategy)

    def test_kind(self) -> None:
        assert _strategy().kind() is EnvironmentType.MANIFEST


class TestScaffold:
    async def test_scaffold_seeds_when_absent(self, tmp_path: Path) -> None:
        strategy = _strategy()
        assert strategy.detect(tmp_path) is False

        result = await strategy.scaffold(tmp_path)

        assert result.seeded is True
        assert result.files_written == (_MANIFEST,)
        assert strategy.detect(tmp_path) is True

    async def test_scaffold_idempotent(self, tmp_path: Path) -> None:
        strategy = _strategy()
        await strategy.scaffold(tmp_path)
        original = (tmp_path / _MANIFEST).read_text(encoding="utf-8")

        result = await strategy.scaffold(tmp_path)

        assert result.seeded is False
        assert result.files_written == ()
        assert (tmp_path / _MANIFEST).read_text(encoding="utf-8") == original


class TestDeclarationHash:
    async def test_hash_stable_for_same_content(self, tmp_path: Path) -> None:
        strategy = _strategy()
        await strategy.scaffold(tmp_path)
        assert strategy.declaration_hash(tmp_path) == strategy.declaration_hash(
            tmp_path
        )

    async def test_hash_changes_when_manifest_edited(self, tmp_path: Path) -> None:
        strategy = _strategy()
        await strategy.scaffold(tmp_path)
        before = strategy.declaration_hash(tmp_path)

        _write_manifest(
            tmp_path,
            'language: python\ntest_command: "pytest"\nsetup_commands: ["echo x"]\n',
        )
        assert strategy.declaration_hash(tmp_path) != before

    async def test_hash_changes_when_lockfile_changes(self, tmp_path: Path) -> None:
        strategy = _strategy()
        _write_manifest(
            tmp_path,
            'language: python\ntest_command: "pytest"\nlockfiles: ["req.lock"]\n',
        )
        (tmp_path / "req.lock").write_text("a==1\n", encoding="utf-8")
        before = strategy.declaration_hash(tmp_path)

        (tmp_path / "req.lock").write_text("a==2\n", encoding="utf-8")
        assert strategy.declaration_hash(tmp_path) != before

    def test_hash_missing_manifest_raises(self, tmp_path: Path) -> None:
        with pytest.raises(EnvironmentConfigError):
            _strategy().declaration_hash(tmp_path)

    def test_missing_lockfile_on_disk_is_tolerated(self, tmp_path: Path) -> None:
        # A declared-but-absent lockfile still feeds its path into the
        # hash; creating it later (changing content) re-provisions.
        strategy = _strategy()
        _write_manifest(
            tmp_path,
            'language: python\ntest_command: "pytest"\nlockfiles: ["req.lock"]\n',
        )
        before = strategy.declaration_hash(tmp_path)
        (tmp_path / "req.lock").write_text("a==1\n", encoding="utf-8")
        assert strategy.declaration_hash(tmp_path) != before

    @pytest.mark.parametrize("escape", ["../outside_secret.txt", "sub/../../escape"])
    def test_traversal_lockfile_not_read(self, tmp_path: Path, escape: str) -> None:
        # A lockfile path escaping the workspace is rejected: its bytes
        # never enter the hash, so editing the resolved escaped target is
        # invisible. The workspace is a nested directory so the escaped
        # target resolves inside this test's unique ``tmp_path`` rather
        # than the shared xdist worker temp dir -- otherwise parallel
        # tests collide on the resolved path (e.g. ``<worker>/escape``).
        workspace = tmp_path / "ws"
        workspace.mkdir()
        escaped = (workspace / escape).resolve()
        escaped.parent.mkdir(parents=True, exist_ok=True)
        escaped.write_text("v1", encoding="utf-8")
        strategy = _strategy()
        _write_manifest(
            workspace,
            f'language: python\ntest_command: "pytest"\nlockfiles: ["{escape}"]\n',
        )
        before = strategy.declaration_hash(workspace)
        escaped.write_text("v2-changed", encoding="utf-8")
        assert strategy.declaration_hash(workspace) == before

    def test_absolute_lockfile_not_read(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "abs_secret.txt"
        outside.write_text("v1", encoding="utf-8")
        strategy = _strategy()
        _write_manifest(
            tmp_path,
            f'language: python\ntest_command: "pytest"\n'
            f'lockfiles: ["{outside.as_posix()}"]\n',
        )
        before = strategy.declaration_hash(tmp_path)
        outside.write_text("v2-changed", encoding="utf-8")
        assert strategy.declaration_hash(tmp_path) == before


class TestReadManifestValidation:
    def test_non_mapping_rejected(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, "- just\n- a\n- list\n")
        with pytest.raises(EnvironmentConfigError):
            _strategy().declaration_hash(tmp_path)

    def test_unknown_key_rejected(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path,
            'language: python\ntest_command: "pytest"\nbogus: 1\n',
        )
        with pytest.raises(EnvironmentConfigError):
            _strategy().declaration_hash(tmp_path)


class TestProvision:
    async def test_runs_setup_and_writes_bootstrap(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path,
            'language: python\ntest_command: "python -m pytest"\n'
            'setup_commands: ["echo one", "echo two"]\n'
            'env: {"PATH_EXTRA": "/x"}\n',
        )
        runner = _RecordingRunner()

        result = await _strategy().provision(
            project_id=NotBlankStr("proj-1"),
            workspace_path=tmp_path,
            runner=runner,
            sandbox_kind=NotBlankStr("subprocess"),
        )

        assert result.environment_type is EnvironmentType.MANIFEST
        assert result.image_ref is None
        assert result.env_vars == {"PATH_EXTRA": "/x"}
        # Both setup commands ran, in order, via the shell.
        assert [c[1][-1] for c in runner.calls] == ["echo one", "echo two"]
        # bootstrap.sh was emitted and is reproducible (commands + test hint).
        script = (tmp_path / BOOTSTRAP_SCRIPT_NAME).read_text(encoding="utf-8")
        assert "echo one" in script
        assert "echo two" in script
        assert "python -m pytest" in script
        assert script.startswith("#!/usr/bin/env sh")

    async def test_empty_setup_succeeds_with_noop_bootstrap(
        self, tmp_path: Path
    ) -> None:
        _write_manifest(
            tmp_path,
            'language: python\ntest_command: "pytest"\nsetup_commands: []\n',
        )
        runner = _RecordingRunner()

        result = await _strategy().provision(
            project_id=NotBlankStr("proj-1"),
            workspace_path=tmp_path,
            runner=runner,
            sandbox_kind=NotBlankStr("subprocess"),
        )

        assert runner.calls == []
        assert result.declaration_hash
        assert (tmp_path / BOOTSTRAP_SCRIPT_NAME).is_file()

    async def test_failed_command_raises(self, tmp_path: Path) -> None:
        _write_manifest(
            tmp_path,
            'language: python\ntest_command: "pytest"\n'
            'setup_commands: ["good", "boom", "never"]\n',
        )
        runner = _RecordingRunner(fail_on="boom")

        with pytest.raises(EnvironmentProvisionError, match=r"exit 1") as exc_info:
            await _strategy().provision(
                project_id=NotBlankStr("proj-1"),
                workspace_path=tmp_path,
                runner=runner,
                sandbox_kind=NotBlankStr("subprocess"),
            )
        # Stops at the failing command; the third never runs.
        assert [c[1][-1] for c in runner.calls] == ["good", "boom"]
        # The failing command is named in the error for debuggability.
        assert "boom" in str(exc_info.value)
