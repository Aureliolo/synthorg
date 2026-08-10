"""Unit tests for sandbox backend factory functions."""

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.observability.events.sandbox import SANDBOX_FACTORY_CLEANUP_FAILED
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.sandbox.config import SubprocessSandboxConfig
from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.factory import (
    build_sandbox_backends,
    cleanup_sandbox_backends,
    merge_gvisor_defaults,
    merge_secure_backend_defaults,
    resolve_sandbox_for_category,
)
from synthorg.tools.sandbox.lifecycle.per_call import PerCallStrategy
from synthorg.tools.sandbox.protocol import SandboxBackend
from synthorg.tools.sandbox.sandboxing_config import (
    UNTRUSTED_EXEC_CATEGORIES,
    SandboxingConfig,
)

pytestmark = pytest.mark.unit


class TestUntrustedExecOverride:
    """An override cannot put agent-authored code back on the host.

    The container is the boundary rather than a second one behind an
    approval, because the supervised preset auto-approves ``code:*``. So the
    downgrade is refused where the config is built, not weighed later.
    """

    def test_another_category_keeps_operator_precedence(self) -> None:
        config = SandboxingConfig(
            default_backend="docker",
            overrides={"file_system": "subprocess"},
        )

        assert config.backend_for_category("file_system") == "subprocess"

    def test_the_forcing_function_and_the_refusal_share_one_declaration(
        self,
    ) -> None:
        """Two lists would let the boundary and its enforcement disagree."""
        forced = merge_secure_backend_defaults(
            SandboxingConfig(default_backend="subprocess")
        )

        assert {
            category
            for category, backend in forced.overrides.items()
            if backend == "docker"
        } == set(UNTRUSTED_EXEC_CATEGORIES)


class TestBuildSandboxBackends:
    """Tests for build_sandbox_backends()."""

    @patch("synthorg.tools.sandbox.factory.SubprocessSandbox")
    def test_default_subprocess_builds_only_subprocess(
        self,
        mock_subprocess_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Default config (subprocess) builds only SubprocessSandbox."""
        config = SandboxingConfig(default_backend="subprocess")
        backends = build_sandbox_backends(config=config, workspace=tmp_path)

        assert "subprocess" in backends
        assert "docker" not in backends
        mock_subprocess_cls.assert_called_once_with(
            config=config.subprocess,
            workspace=tmp_path,
        )

    @patch("synthorg.tools.sandbox.factory.DockerSandbox")
    def test_default_docker_builds_only_docker(
        self,
        mock_docker_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Docker default builds only DockerSandbox."""
        config = SandboxingConfig(default_backend="docker")
        backends = build_sandbox_backends(config=config, workspace=tmp_path)

        assert "docker" in backends
        assert "subprocess" not in backends
        mock_docker_cls.assert_called_once_with(
            config=config.docker,
            workspace=tmp_path,
            tracked_container_repo=None,
            lifecycle_strategy=None,
        )

    @patch("synthorg.tools.sandbox.factory.DockerSandbox")
    def test_injected_lifecycle_strategy_forwarded_to_docker(
        self,
        mock_docker_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """A provided lifecycle strategy is forwarded to DockerSandbox.

        The ``None`` cases only prove the fallback; this guards the
        boot-time wiring that injects the configured strategy so it
        cannot regress silently.
        """
        strategy = PerCallStrategy()
        config = SandboxingConfig(default_backend="docker")
        build_sandbox_backends(
            config=config,
            workspace=tmp_path,
            lifecycle_strategy=strategy,
        )

        mock_docker_cls.assert_called_once_with(
            config=config.docker,
            workspace=tmp_path,
            tracked_container_repo=None,
            lifecycle_strategy=strategy,
        )

    @patch("synthorg.tools.sandbox.factory.DockerSandbox")
    @patch("synthorg.tools.sandbox.factory.SubprocessSandbox")
    def test_mixed_overrides_builds_both(
        self,
        mock_subprocess_cls: MagicMock,
        mock_docker_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Default subprocess + code_execution override docker builds both."""
        config = SandboxingConfig(
            default_backend="subprocess",
            overrides={"code_execution": "docker"},
        )
        backends = build_sandbox_backends(config=config, workspace=tmp_path)

        assert "subprocess" in backends
        assert "docker" in backends
        mock_subprocess_cls.assert_called_once()
        mock_docker_cls.assert_called_once()

    @patch("synthorg.tools.sandbox.factory.SubprocessSandbox")
    def test_backends_use_correct_sub_configs(
        self,
        mock_subprocess_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Backend instances receive their sub-configs from SandboxingConfig."""
        sub_config = SubprocessSandboxConfig(timeout_seconds=120)
        config = SandboxingConfig(
            default_backend="subprocess",
            subprocess=sub_config,
        )
        build_sandbox_backends(config=config, workspace=tmp_path)

        mock_subprocess_cls.assert_called_once_with(
            config=sub_config,
            workspace=tmp_path,
        )

    @patch("synthorg.tools.sandbox.factory.DockerSandbox")
    def test_docker_backend_uses_docker_sub_config(
        self,
        mock_docker_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Docker backend receives docker sub-config."""
        docker_config = DockerSandboxConfig(image="test-image:latest")
        config = SandboxingConfig(
            default_backend="docker",
            docker=docker_config,
        )
        build_sandbox_backends(config=config, workspace=tmp_path)

        mock_docker_cls.assert_called_once_with(
            config=docker_config,
            workspace=tmp_path,
            tracked_container_repo=None,
            lifecycle_strategy=None,
        )

    @patch("synthorg.tools.sandbox.factory.SubprocessSandbox")
    def test_only_needed_backends_instantiated(
        self,
        mock_subprocess_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When all overrides use the same backend, only that one is built."""
        config = SandboxingConfig(
            default_backend="subprocess",
            overrides={
                "version_control": "subprocess",
                "file_system": "subprocess",
            },
        )
        backends = build_sandbox_backends(config=config, workspace=tmp_path)

        assert set(backends.keys()) == {"subprocess"}
        mock_subprocess_cls.assert_called_once()

    @patch("synthorg.tools.sandbox.factory.DockerSandbox")
    @patch("synthorg.tools.sandbox.factory.SubprocessSandbox")
    def test_returns_mapping_proxy_with_correct_keys(
        self,
        mock_subprocess_cls: MagicMock,
        mock_docker_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Return type is a MappingProxyType with correct keys and values."""
        config = SandboxingConfig(
            default_backend="subprocess",
            overrides={"code_execution": "docker"},
        )
        backends = build_sandbox_backends(config=config, workspace=tmp_path)

        assert isinstance(backends, MappingProxyType)
        assert set(backends.keys()) == {"subprocess", "docker"}
        assert backends["subprocess"] is mock_subprocess_cls.return_value
        assert backends["docker"] is mock_docker_cls.return_value


class TestResolveSandboxForCategory:
    """Tests for resolve_sandbox_for_category()."""

    def test_returns_override_backend_for_overridden_category(
        self,
    ) -> None:
        """Category with override returns the override backend."""
        mock_subprocess = MagicMock(spec=SandboxBackend)
        mock_docker = MagicMock(spec=SandboxBackend)
        config = SandboxingConfig(
            default_backend="subprocess",
            overrides={"code_execution": "docker"},
        )
        backends: Mapping[str, SandboxBackend] = {
            "subprocess": mock_subprocess,
            "docker": mock_docker,
        }

        result = resolve_sandbox_for_category(
            config=config,
            backends=backends,
            category=ToolCategory.CODE_EXECUTION,
        )
        assert result is mock_docker

    def test_falls_back_to_default_for_non_overridden_category(
        self,
    ) -> None:
        """Category without override falls back to default backend."""
        mock_subprocess = MagicMock(spec=SandboxBackend)
        config = SandboxingConfig(default_backend="subprocess")
        backends: Mapping[str, SandboxBackend] = {
            "subprocess": mock_subprocess,
        }

        result = resolve_sandbox_for_category(
            config=config,
            backends=backends,
            category=ToolCategory.VERSION_CONTROL,
        )
        assert result is mock_subprocess

    @pytest.mark.parametrize(
        "category",
        list(ToolCategory),
        ids=[c.name for c in ToolCategory],
    )
    def test_works_with_all_tool_categories(
        self,
        category: ToolCategory,
    ) -> None:
        """Every ToolCategory value resolves without error."""
        mock_backend = MagicMock(spec=SandboxBackend)
        config = SandboxingConfig(default_backend="subprocess")
        backends: Mapping[str, SandboxBackend] = {
            "subprocess": mock_backend,
        }

        result = resolve_sandbox_for_category(
            config=config,
            backends=backends,
            category=category,
        )
        assert result is mock_backend

    def test_raises_key_error_when_backend_missing(self) -> None:
        """KeyError with descriptive message when backend not in map."""
        config = SandboxingConfig(
            default_backend="subprocess",
            overrides={"version_control": "docker"},
        )
        backends: Mapping[str, SandboxBackend] = {
            "subprocess": MagicMock(spec=SandboxBackend),
        }

        with pytest.raises(
            KeyError,
            match=(
                r"resolved for category 'version_control'"
                r".*available: \['subprocess'\]"
            ),
        ):
            resolve_sandbox_for_category(
                config=config,
                backends=backends,
                category=ToolCategory.VERSION_CONTROL,
            )


class TestCleanupSandboxBackends:
    """Tests for cleanup_sandbox_backends()."""

    async def test_calls_cleanup_on_all_backends(self) -> None:
        """cleanup() is called on every backend in the mapping."""
        mock_sub = AsyncMock(spec=SandboxBackend)
        mock_docker = AsyncMock(spec=SandboxBackend)
        backends: Mapping[str, SandboxBackend] = {
            "subprocess": mock_sub,
            "docker": mock_docker,
        }

        await cleanup_sandbox_backends(backends=backends)

        mock_sub.cleanup.assert_awaited_once()
        mock_docker.cleanup.assert_awaited_once()

    async def test_cleanup_empty_mapping(self) -> None:
        """No error when cleaning up an empty mapping."""
        await cleanup_sandbox_backends(backends={})

    async def test_cleanup_single_backend(self) -> None:
        """Cleanup works with a single backend."""
        mock_backend = AsyncMock(spec=SandboxBackend)
        backends: Mapping[str, SandboxBackend] = {
            "subprocess": mock_backend,
        }

        await cleanup_sandbox_backends(backends=backends)

        mock_backend.cleanup.assert_awaited_once()

    async def test_cleanup_continues_on_error(self) -> None:
        """All backends are cleaned up even when one raises."""
        failing = AsyncMock(spec=SandboxBackend)
        failing.cleanup.side_effect = RuntimeError("container gone")
        healthy = AsyncMock(spec=SandboxBackend)

        backends: Mapping[str, SandboxBackend] = {
            "broken": failing,
            "ok": healthy,
        }

        with patch(
            "synthorg.tools.sandbox.factory.logger",
        ) as mock_logger:
            # Should not raise -- errors are logged and swallowed
            await cleanup_sandbox_backends(backends=backends)

        failing.cleanup.assert_awaited_once()
        healthy.cleanup.assert_awaited_once()
        # Verify the error was actually logged
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert call_args[0][0] == SANDBOX_FACTORY_CLEANUP_FAILED
        assert call_args[1]["backend"] == "broken"


class TestMergeGvisorDefaults:
    """Tests for merge_gvisor_defaults()."""

    def test_injects_gvisor_defaults_for_docker_backend(self) -> None:
        """Default gVisor overrides are injected for Docker backends."""
        config = SandboxingConfig(
            default_backend="subprocess",
            overrides={"code_execution": "docker"},
        )
        merged = merge_gvisor_defaults(config)
        assert merged.docker.runtime_overrides["code_execution"] == "runsc"
        assert merged.docker.runtime_overrides["terminal"] == "runsc"

    def test_user_overrides_take_precedence(self) -> None:
        """User-supplied runtime_overrides are not overwritten."""
        docker_config = DockerSandboxConfig(
            runtime_overrides={"code_execution": "runc"},
        )
        config = SandboxingConfig(
            default_backend="docker",
            docker=docker_config,
        )
        merged = merge_gvisor_defaults(config)
        assert merged.docker.runtime_overrides["code_execution"] == "runc"
        assert merged.docker.runtime_overrides["terminal"] == "runsc"

    def test_no_change_when_docker_not_referenced(self) -> None:
        """Config is returned unchanged when Docker is not needed."""
        config = SandboxingConfig(default_backend="subprocess")
        merged = merge_gvisor_defaults(config)
        assert merged is config

    def test_no_change_when_user_already_set_all(self) -> None:
        """No copy when user already set all gVisor defaults."""
        docker_config = DockerSandboxConfig(
            runtime_overrides={
                "code_execution": "runsc",
                "terminal": "runsc",
            },
        )
        config = SandboxingConfig(
            default_backend="docker",
            docker=docker_config,
        )
        merged = merge_gvisor_defaults(config)
        assert merged is config


class TestMergeSecureBackendDefaults:
    """Tests for merge_secure_backend_defaults()."""

    def test_forces_untrusted_categories_to_docker(self) -> None:
        """code_execution + terminal route to docker even on a subprocess default."""
        config = SandboxingConfig(default_backend="subprocess")
        merged = merge_secure_backend_defaults(config)
        assert merged.backend_for_category("code_execution") == "docker"
        assert merged.backend_for_category("terminal") == "docker"
        # The global default is untouched for low-risk categories.
        assert merged.default_backend == "subprocess"
        assert merged.backend_for_category("file_read") == "subprocess"

    def test_an_operator_override_wins_outside_the_untrusted_set(self) -> None:
        """Precedence is intact everywhere the container is not the boundary."""
        config = SandboxingConfig(
            default_backend="docker",
            overrides={"file_system": "subprocess"},
        )
        merged = merge_secure_backend_defaults(config)
        assert merged.backend_for_category("file_system") == "subprocess"
        assert merged.backend_for_category("code_execution") == "docker"

    def test_gvisor_defaults_applied_when_already_routed(self) -> None:
        """Already-routed untrusted categories still gain gVisor runtimes.

        ``merge_secure_backend_defaults`` always layers the hardened gVisor
        runtime once docker is referenced, so code_execution/terminal never
        run on plain docker even when an override already pinned the backend.
        """
        config = SandboxingConfig(
            default_backend="subprocess",
            overrides={"code_execution": "docker", "terminal": "docker"},
        )
        merged = merge_secure_backend_defaults(config)
        assert merged.backend_for_category("code_execution") == "docker"
        assert merged.backend_for_category("terminal") == "docker"
        assert merged.docker.runtime_overrides["code_execution"] == "runsc"
        assert merged.docker.runtime_overrides["terminal"] == "runsc"
