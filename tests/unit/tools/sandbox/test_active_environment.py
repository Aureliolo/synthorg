"""Tests for the ambient per-task sandbox environment threading."""

import sys
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from synthorg.core.types import NotBlankStr
from synthorg.tools.sandbox.active_environment import (
    ActiveSandboxEnvironment,
    active_sandbox_environment,
    get_active_sandbox_environment,
)
from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox
from synthorg.tools.sandbox.subprocess_sandbox import SubprocessSandbox
from tests._shared import JsonDict

pytestmark = pytest.mark.unit


class TestActiveSandboxEnvironmentContextVar:
    def test_default_is_none(self) -> None:
        assert get_active_sandbox_environment() is None

    def test_set_and_reset(self) -> None:
        env = ActiveSandboxEnvironment(image_override=NotBlankStr("img:1"))
        with active_sandbox_environment(env):
            assert get_active_sandbox_environment() is env
        assert get_active_sandbox_environment() is None

    def test_nesting_restores_outer(self) -> None:
        outer = ActiveSandboxEnvironment(image_override=NotBlankStr("outer:1"))
        inner = ActiveSandboxEnvironment(image_override=NotBlankStr("inner:1"))
        with active_sandbox_environment(outer):
            with active_sandbox_environment(inner):
                assert get_active_sandbox_environment() is inner
            assert get_active_sandbox_environment() is outer
        assert get_active_sandbox_environment() is None

    def test_frozen(self) -> None:
        env = ActiveSandboxEnvironment()
        with pytest.raises(ValidationError):
            env.image_override = NotBlankStr("x")  # type: ignore[misc]


class TestDockerImageOverride:
    def test_build_container_config_uses_image_override(self, tmp_path: Path) -> None:
        sandbox = DockerSandbox(config=DockerSandboxConfig(), workspace=tmp_path)
        config = sandbox._build_container_config(
            command="echo",
            args=("hi",),
            container_cwd="/workspace",
            env_overrides={"FOO": "bar"},
            image_override=NotBlankStr("synthorg-project-p:abc123"),
        )
        assert config["Image"] == "synthorg-project-p:abc123"
        assert "FOO=bar" in cast(JsonDict, config)["Env"]

    def test_build_container_config_default_image(self, tmp_path: Path) -> None:
        cfg = DockerSandboxConfig()
        sandbox = DockerSandbox(config=cfg, workspace=tmp_path)
        config = sandbox._build_container_config(
            command="echo",
            args=(),
            container_cwd="/workspace",
            env_overrides=None,
        )
        assert config["Image"] == cfg.image


class TestSubprocessEnvAdditions:
    async def test_env_additions_reach_subprocess(self, tmp_path: Path) -> None:
        sandbox = SubprocessSandbox(workspace=tmp_path)
        env = ActiveSandboxEnvironment(
            env_additions={"SYNTHORG_ENV_TEST": "present"},
        )
        with active_sandbox_environment(env):
            result = await sandbox.execute(
                command=sys.executable,
                args=(
                    "-c",
                    "import os; print(os.environ.get('SYNTHORG_ENV_TEST', 'MISSING'))",
                ),
            )
        assert result.success
        assert "present" in result.stdout

    async def test_no_active_env_leaves_subprocess_clean(self, tmp_path: Path) -> None:
        sandbox = SubprocessSandbox(workspace=tmp_path)
        result = await sandbox.execute(
            command=sys.executable,
            args=(
                "-c",
                "import os; print(os.environ.get('SYNTHORG_ENV_TEST', 'MISSING'))",
            ),
        )
        assert result.success
        assert "MISSING" in result.stdout
