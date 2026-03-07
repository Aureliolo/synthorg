"""Tests for SubprocessSandbox implementation."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from ai_company.tools.sandbox.config import SubprocessSandboxConfig
from ai_company.tools.sandbox.errors import SandboxError, SandboxStartError
from ai_company.tools.sandbox.subprocess_sandbox import SubprocessSandbox

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


# ── Constructor ──────────────────────────────────────────────────


class TestSubprocessSandboxInit:
    """Constructor validation."""

    def test_workspace_must_be_absolute(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="absolute path"):
            SubprocessSandbox(workspace=Path("relative"))

    def test_workspace_must_exist(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent"
        with pytest.raises(ValueError, match="does not exist"):
            SubprocessSandbox(workspace=missing)

    def test_valid_workspace(self, tmp_path: Path) -> None:
        sandbox = SubprocessSandbox(workspace=tmp_path)
        assert sandbox.workspace == tmp_path.resolve()

    def test_default_config(self, tmp_path: Path) -> None:
        sandbox = SubprocessSandbox(workspace=tmp_path)
        assert sandbox.config.timeout_seconds == 30.0


# ── Environment filtering ───────────────────────────────────────


class TestEnvironmentFiltering:
    """Environment variable filtering and denylist enforcement."""

    def test_secrets_stripped(
        self,
        subprocess_sandbox: SubprocessSandbox,
    ) -> None:
        with patch.dict(
            os.environ,
            {"API_KEY": "secret123", "HOME": "/home/test"},
            clear=True,
        ):
            env = subprocess_sandbox._build_filtered_env()
            assert "API_KEY" not in env
            assert "HOME" in env

    def test_allowlist_passes_through(
        self,
        sandbox_workspace: Path,
    ) -> None:
        config = SubprocessSandboxConfig(
            env_allowlist=("CUSTOM_VAR",),
            env_denylist_patterns=(),
        )
        sandbox = SubprocessSandbox(
            config=config,
            workspace=sandbox_workspace,
        )
        with patch.dict(
            os.environ,
            {"CUSTOM_VAR": "yes", "OTHER": "no"},
            clear=True,
        ):
            env = sandbox._build_filtered_env()
            assert env["CUSTOM_VAR"] == "yes"
            assert "OTHER" not in env

    def test_denylist_overrides_allowlist(
        self,
        sandbox_workspace: Path,
    ) -> None:
        config = SubprocessSandboxConfig(
            env_allowlist=("MY_SECRET_KEY",),
            env_denylist_patterns=("*SECRET*",),
        )
        sandbox = SubprocessSandbox(
            config=config,
            workspace=sandbox_workspace,
        )
        with patch.dict(
            os.environ,
            {"MY_SECRET_KEY": "hidden"},
            clear=True,
        ):
            env = sandbox._build_filtered_env()
            assert "MY_SECRET_KEY" not in env

    def test_env_overrides_applied(
        self,
        subprocess_sandbox: SubprocessSandbox,
    ) -> None:
        with patch.dict(os.environ, {}, clear=True):
            env = subprocess_sandbox._build_filtered_env(
                env_overrides={"GIT_TERMINAL_PROMPT": "0"},
            )
            assert env["GIT_TERMINAL_PROMPT"] == "0"

    def test_env_overrides_bypass_denylist(
        self,
        sandbox_workspace: Path,
    ) -> None:
        """env_overrides bypass denylist by design (security contract)."""
        config = SubprocessSandboxConfig(
            env_allowlist=("HOME",),
            env_denylist_patterns=("*KEY*",),
        )
        sandbox = SubprocessSandbox(
            config=config,
            workspace=sandbox_workspace,
        )
        with patch.dict(
            os.environ,
            {"HOME": "/home/test"},
            clear=True,
        ):
            env = sandbox._build_filtered_env(
                env_overrides={"API_KEY": "override_value"},
            )
            assert env["API_KEY"] == "override_value"

    def test_lc_glob_matching(
        self,
        sandbox_workspace: Path,
    ) -> None:
        config = SubprocessSandboxConfig(
            env_allowlist=("LC_*",),
            env_denylist_patterns=(),
            restricted_path=False,
        )
        sandbox = SubprocessSandbox(
            config=config,
            workspace=sandbox_workspace,
        )
        with patch.dict(
            os.environ,
            {"LC_ALL": "en_US.UTF-8", "HOME": "/home/test"},
            clear=True,
        ):
            env = sandbox._build_filtered_env()
            assert "LC_ALL" in env
            assert "HOME" not in env

    def test_restricted_path_filters_entries(
        self,
        sandbox_workspace: Path,
    ) -> None:
        config = SubprocessSandboxConfig(restricted_path=True)
        sandbox = SubprocessSandbox(
            config=config,
            workspace=sandbox_workspace,
        )
        if os.name == "nt":
            fake_path = r"C:\WINDOWS\system32;C:\suspicious\dir"
        else:
            fake_path = "/usr/bin:/suspicious/dir"
        with patch.dict(
            os.environ,
            {"PATH": fake_path},
            clear=True,
        ):
            env = sandbox._build_filtered_env()
            assert "suspicious" not in env.get("PATH", "").lower()

    def test_restricted_path_rejects_prefix_spoofing(
        self,
        sandbox_workspace: Path,
    ) -> None:
        """PATH entries like /usr/bin-malicious are rejected."""
        config = SubprocessSandboxConfig(restricted_path=True)
        sandbox = SubprocessSandbox(
            config=config,
            workspace=sandbox_workspace,
        )
        if os.name == "nt":
            spoofed = r"C:\WINDOWS\system32;C:\WINDOWS-extra\bin"
        else:
            spoofed = "/usr/bin:/usr/bin-malicious"
        with patch.dict(
            os.environ,
            {"PATH": spoofed},
            clear=True,
        ):
            env = sandbox._build_filtered_env()
            assert "malicious" not in env.get("PATH", "").lower()
            assert "extra" not in env.get("PATH", "").lower()

    def test_path_fallback_when_no_entries_match(
        self,
        sandbox_workspace: Path,
    ) -> None:
        """PATH fallback uses safe directories that exist."""
        config = SubprocessSandboxConfig(restricted_path=True)
        sandbox = SubprocessSandbox(
            config=config,
            workspace=sandbox_workspace,
        )
        with patch.dict(
            os.environ,
            {"PATH": "/totally/fake/dir"},
            clear=True,
        ):
            env = sandbox._build_filtered_env()
            assert "/totally/fake/dir" not in env.get("PATH", "")


# ── Workspace boundary ───────────────────────────────────────────


class TestWorkspaceBoundary:
    """Workspace cwd validation."""

    def test_traversal_blocked(
        self,
        subprocess_sandbox: SubprocessSandbox,
    ) -> None:
        outside = subprocess_sandbox.workspace.parent / "outside"
        outside.mkdir(exist_ok=True)
        with pytest.raises(SandboxError, match="outside workspace"):
            subprocess_sandbox._validate_cwd(outside)

    def test_valid_cwd_accepted(
        self,
        subprocess_sandbox: SubprocessSandbox,
    ) -> None:
        subdir = subprocess_sandbox.workspace / "sub"
        subdir.mkdir()
        subprocess_sandbox._validate_cwd(subdir)

    def test_workspace_root_accepted(
        self,
        subprocess_sandbox: SubprocessSandbox,
    ) -> None:
        subprocess_sandbox._validate_cwd(subprocess_sandbox.workspace)

    def test_workspace_only_disabled(
        self,
        sandbox_workspace: Path,
    ) -> None:
        config = SubprocessSandboxConfig(workspace_only=False)
        sandbox = SubprocessSandbox(
            config=config,
            workspace=sandbox_workspace,
        )
        outside = sandbox_workspace.parent / "outside"
        outside.mkdir(exist_ok=True)
        # Should not raise
        sandbox._validate_cwd(outside)


# ── Execution ────────────────────────────────────────────────────


class TestExecution:
    """Command execution tests."""

    async def test_successful_command(
        self,
        subprocess_sandbox: SubprocessSandbox,
    ) -> None:
        if os.name == "nt":
            result = await subprocess_sandbox.execute(
                command="cmd",
                args=("/c", "echo", "hello"),
            )
        else:
            result = await subprocess_sandbox.execute(
                command="echo",
                args=("hello",),
            )
        assert result.success
        assert "hello" in result.stdout

    async def test_failed_command(
        self,
        subprocess_sandbox: SubprocessSandbox,
    ) -> None:
        if os.name == "nt":
            result = await subprocess_sandbox.execute(
                command="cmd",
                args=("/c", "exit", "1"),
            )
        else:
            result = await subprocess_sandbox.execute(
                command="sh",
                args=("-c", "exit 1"),
            )
        assert not result.success
        assert result.returncode != 0

    async def test_timeout_kills_process(
        self,
        subprocess_sandbox: SubprocessSandbox,
    ) -> None:
        if os.name == "nt":
            result = await subprocess_sandbox.execute(
                command="cmd",
                args=("/c", "ping", "-n", "10", "127.0.0.1"),
                timeout=0.5,
            )
        else:
            result = await subprocess_sandbox.execute(
                command="sleep",
                args=("10",),
                timeout=0.5,
            )
        assert result.timed_out
        assert not result.success

    async def test_zero_timeout_kills_process(
        self,
        subprocess_sandbox: SubprocessSandbox,
    ) -> None:
        """timeout=0.0 is treated as immediate timeout, not default."""
        if os.name == "nt":
            result = await subprocess_sandbox.execute(
                command="cmd",
                args=("/c", "ping", "-n", "10", "127.0.0.1"),
                timeout=0.0,
            )
        else:
            result = await subprocess_sandbox.execute(
                command="sleep",
                args=("10",),
                timeout=0.0,
            )
        assert result.timed_out
        assert not result.success

    @pytest.mark.filterwarnings("ignore::ResourceWarning")
    async def test_start_failure(
        self,
        subprocess_sandbox: SubprocessSandbox,
    ) -> None:
        with pytest.raises(SandboxStartError, match="Failed to start"):
            await subprocess_sandbox.execute(
                command="nonexistent_binary_xyz",
                args=(),
            )

    async def test_default_cwd_is_workspace(
        self,
        subprocess_sandbox: SubprocessSandbox,
    ) -> None:
        if os.name == "nt":
            result = await subprocess_sandbox.execute(
                command="cmd",
                args=("/c", "cd"),
            )
        else:
            result = await subprocess_sandbox.execute(
                command="pwd",
                args=(),
            )
        assert result.success
        # Compare resolved paths (noqa: pathlib in async is fine for tests)
        expected = subprocess_sandbox.workspace.resolve()
        actual = Path(result.stdout.strip()).resolve()  # noqa: ASYNC240
        assert actual == expected

    async def test_custom_cwd_within_workspace(
        self,
        subprocess_sandbox: SubprocessSandbox,
    ) -> None:
        subdir = subprocess_sandbox.workspace / "sub"
        subdir.mkdir()
        if os.name == "nt":
            result = await subprocess_sandbox.execute(
                command="cmd",
                args=("/c", "cd"),
                cwd=subdir,
            )
        else:
            result = await subprocess_sandbox.execute(
                command="pwd",
                args=(),
                cwd=subdir,
            )
        assert result.success
        actual = Path(result.stdout.strip()).resolve()  # noqa: ASYNC240
        assert actual == subdir.resolve()

    async def test_cwd_outside_workspace_blocked(
        self,
        subprocess_sandbox: SubprocessSandbox,
    ) -> None:
        outside = subprocess_sandbox.workspace.parent / "outside"
        outside.mkdir(exist_ok=True)
        with pytest.raises(SandboxError, match="outside workspace"):
            await subprocess_sandbox.execute(
                command="echo",
                args=("test",),
                cwd=outside,
            )


# ── Health check & cleanup ───────────────────────────────────────


class TestHealthCheckAndCleanup:
    """Health check and cleanup behavior."""

    async def test_health_check_valid_workspace(
        self,
        subprocess_sandbox: SubprocessSandbox,
    ) -> None:
        assert await subprocess_sandbox.health_check() is True

    async def test_health_check_missing_workspace(
        self,
        tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "exists"
        workspace.mkdir()
        sandbox = SubprocessSandbox(workspace=workspace)
        workspace.rmdir()
        assert await sandbox.health_check() is False

    async def test_cleanup_is_noop(
        self,
        subprocess_sandbox: SubprocessSandbox,
    ) -> None:
        # Should not raise
        await subprocess_sandbox.cleanup()

    def test_backend_type(
        self,
        subprocess_sandbox: SubprocessSandbox,
    ) -> None:
        assert subprocess_sandbox.get_backend_type() == "subprocess"


# ── extra_safe_path_prefixes ─────────────────────────────────────


class TestExtraSafePathPrefixes:
    """Tests for configurable safe PATH prefixes."""

    def test_extra_prefixes_included(
        self,
        sandbox_workspace: Path,
    ) -> None:
        extra = (r"C:\CustomTools",) if os.name == "nt" else ("/opt/custom/bin",)
        config = SubprocessSandboxConfig(
            extra_safe_path_prefixes=extra,
        )
        sandbox = SubprocessSandbox(
            config=config,
            workspace=sandbox_workspace,
        )
        prefixes = sandbox._get_safe_path_prefixes()
        assert extra[0] in prefixes

    def test_extra_prefix_survives_path_filter(
        self,
        sandbox_workspace: Path,
    ) -> None:
        extra, fake_path = (
            (
                (r"C:\CustomTools",),
                r"C:\CustomTools\bin;C:\suspicious\dir",
            )
            if os.name == "nt"
            else (
                ("/opt/custom",),
                "/opt/custom/bin:/suspicious/dir",
            )
        )
        config = SubprocessSandboxConfig(
            restricted_path=True,
            extra_safe_path_prefixes=extra,
        )
        sandbox = SubprocessSandbox(
            config=config,
            workspace=sandbox_workspace,
        )
        with patch.dict(
            os.environ,
            {"PATH": fake_path},
            clear=True,
        ):
            env = sandbox._build_filtered_env()
            assert "suspicious" not in env.get("PATH", "").lower()
            assert "custom" in env.get("PATH", "").lower()

    def test_default_empty_no_change(
        self,
        sandbox_workspace: Path,
    ) -> None:
        config = SubprocessSandboxConfig()
        sandbox = SubprocessSandbox(
            config=config,
            workspace=sandbox_workspace,
        )
        defaults_only = SubprocessSandbox(
            workspace=sandbox_workspace,
        )
        assert (
            sandbox._get_safe_path_prefixes() == defaults_only._get_safe_path_prefixes()
        )

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValidationError, match="non-empty absolute"):
            SubprocessSandboxConfig(extra_safe_path_prefixes=("",))

    def test_rejects_relative_path(self) -> None:
        with pytest.raises(ValidationError, match="non-empty absolute"):
            SubprocessSandboxConfig(
                extra_safe_path_prefixes=("relative/path",),
            )
