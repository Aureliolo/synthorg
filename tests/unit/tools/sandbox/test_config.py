"""Tests for SubprocessSandboxConfig model."""

import pytest
from pydantic import ValidationError

from synthorg.tools.sandbox.config import SubprocessSandboxConfig

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


class TestSubprocessSandboxConfig:
    """SubprocessSandboxConfig defaults, validation, and immutability."""

    def test_defaults(self) -> None:
        config = SubprocessSandboxConfig()
        assert config.timeout_seconds == 30.0
        assert config.workspace_only is True
        assert config.restricted_path is True
        assert isinstance(config.env_allowlist, tuple)
        assert isinstance(config.env_denylist_patterns, tuple)

    def test_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            SubprocessSandboxConfig(timeout_seconds=0)

    def test_timeout_must_not_exceed_max(self) -> None:
        with pytest.raises(ValidationError):
            SubprocessSandboxConfig(timeout_seconds=601)

    def test_timeout_at_max_boundary(self) -> None:
        config = SubprocessSandboxConfig(timeout_seconds=600)
        assert config.timeout_seconds == 600

    def test_custom_allowlist(self) -> None:
        config = SubprocessSandboxConfig(
            env_allowlist=("HOME", "CUSTOM"),
        )
        assert config.env_allowlist == ("HOME", "CUSTOM")

    def test_custom_denylist_patterns(self) -> None:
        config = SubprocessSandboxConfig(
            env_denylist_patterns=("*DANGER*",),
        )
        assert config.env_denylist_patterns == ("*DANGER*",)

    def test_frozen(self) -> None:
        config = SubprocessSandboxConfig()
        with pytest.raises(ValidationError):
            config.timeout_seconds = 10.0  # type: ignore[misc]

    def test_env_allowlist_contains_path(self) -> None:
        config = SubprocessSandboxConfig()
        assert "PATH" in config.env_allowlist

    def test_denylist_patterns_cover_secrets(self) -> None:
        config = SubprocessSandboxConfig()
        patterns = config.env_denylist_patterns
        assert any("KEY" in p for p in patterns)
        assert any("SECRET" in p for p in patterns)
        assert any("TOKEN" in p for p in patterns)

    def test_denylist_patterns_cover_library_injection(self) -> None:
        config = SubprocessSandboxConfig()
        patterns = config.env_denylist_patterns
        for var in (
            "LD_PRELOAD",
            "LD_LIBRARY_PATH",
            "DYLD_INSERT_LIBRARIES",
            "PYTHONPATH",
            "NODE_PATH",
            "RUBYLIB",
            "PERL5LIB",
        ):
            assert var in patterns, f"{var} missing from denylist"
