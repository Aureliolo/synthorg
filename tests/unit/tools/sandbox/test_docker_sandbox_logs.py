"""Tests for container log collection, shipping, and env injection.

Tests target the standalone functions in
``synthorg.tools.sandbox.container_log_shipper`` and the
``DockerSandbox`` integration points that use them.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.testing

from synthorg.observability.config import ContainerLogShippingConfig
from synthorg.observability.events.sandbox import (
    SANDBOX_CONTAINER_LOGS_SHIP_FAILED,
    SANDBOX_CONTAINER_LOGS_SHIPPED,
)
from synthorg.tools.sandbox.container_log_shipper import (
    build_correlation_env,
    collect_sidecar_logs,
    parse_json_log_lines,
    ship_container_logs,
)
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox

pytestmark = pytest.mark.unit

_SHIPPER_MODULE = "synthorg.tools.sandbox.container_log_shipper"


# ── Fixtures ────────────────────────────────────────────────────


def _make_log_lines(*entries: dict[str, Any]) -> list[str]:
    """Build Docker log output lines from JSON dicts."""
    return [json.dumps(e) + "\n" for e in entries]


def _env_to_dict(env_list: list[str]) -> dict[str, str]:
    """Convert a Docker env list to a dict for assertions."""
    return dict(item.split("=", 1) for item in env_list)


@pytest.fixture
def mock_docker() -> MagicMock:
    """Pre-configured aiodocker mock."""
    docker = MagicMock()
    docker.containers.container = MagicMock()
    return docker


@pytest.fixture
def default_config() -> ContainerLogShippingConfig:
    """Default log shipping config."""
    return ContainerLogShippingConfig()


@pytest.fixture
def sandbox(tmp_path: object) -> DockerSandbox:
    """DockerSandbox with a temp workspace."""
    return DockerSandbox(workspace=tmp_path)  # type: ignore[arg-type]


def _set_mock_log_output(
    mock_docker: MagicMock,
    lines: list[str],
) -> None:
    """Wire the mock docker to return *lines* from container.log()."""
    container = MagicMock()
    container.log = AsyncMock(return_value=lines)
    mock_docker.containers.container = MagicMock(return_value=container)


# ── parse_json_log_lines ────────────────────────────────────────


class TestParseJsonLogLines:
    """Direct tests for the static parsing function."""

    def test_valid_json_parsed(self) -> None:
        lines = _make_log_lines({"msg": "a"}, {"msg": "b"})
        result = parse_json_log_lines(
            lines,
            max_log_bytes=10_000,
            sidecar_id_short="side",
        )
        assert len(result) == 2
        assert result[0]["msg"] == "a"
        assert result[1]["msg"] == "b"

    def test_malformed_lines_skipped(self) -> None:
        lines = ['{"msg":"good"}\n', "not json\n", '{"msg":"ok"}\n']
        result = parse_json_log_lines(
            lines,
            max_log_bytes=10_000,
            sidecar_id_short="side",
        )
        assert len(result) == 2

    def test_empty_input(self) -> None:
        result = parse_json_log_lines(
            [],
            max_log_bytes=10_000,
            sidecar_id_short="side",
        )
        assert result == ()

    def test_whitespace_only_lines_skipped(self) -> None:
        lines = ["  \n", "\t\n", '{"msg":"ok"}\n']
        result = parse_json_log_lines(
            lines,
            max_log_bytes=10_000,
            sidecar_id_short="side",
        )
        assert len(result) == 1

    def test_non_dict_json_values_skipped(self) -> None:
        lines = [
            '{"msg":"good"}\n',
            "42\n",
            '"just a string"\n',
            "[1, 2, 3]\n",
            '{"msg":"also good"}\n',
        ]
        result = parse_json_log_lines(
            lines,
            max_log_bytes=10_000,
            sidecar_id_short="side",
        )
        assert len(result) == 2
        assert result[0]["msg"] == "good"
        assert result[1]["msg"] == "also good"

    def test_byte_budget_truncation(self) -> None:
        # Each line: {"msg": "aaa..."} ≈ 45 chars.
        lines = _make_log_lines(
            {"msg": "a" * 30},
            {"msg": "b" * 30},
            {"msg": "c" * 30},
        )
        result = parse_json_log_lines(
            lines,
            max_log_bytes=60,
            sidecar_id_short="side",
        )
        # First line (~45 chars) fits; second exceeds 60 cumulative.
        assert len(result) == 1
        assert result[0]["msg"] == "a" * 30


# ── collect_sidecar_logs ────────────────────────────────────────


class TestCollectSidecarLogs:
    """Tests for the standalone collect_sidecar_logs function."""

    async def test_valid_json_lines_parsed(
        self,
        mock_docker: MagicMock,
        default_config: ContainerLogShippingConfig,
    ) -> None:
        lines = _make_log_lines(
            {"ts": "2026-04-14T00:00:00Z", "level": "info", "msg": "started"},
            {"ts": "2026-04-14T00:00:01Z", "level": "debug", "msg": "dns"},
        )
        _set_mock_log_output(mock_docker, lines)

        result = await collect_sidecar_logs(
            mock_docker,
            "sidecar123",
            config=default_config,
        )
        assert len(result) == 2
        assert result[0]["msg"] == "started"
        assert result[1]["msg"] == "dns"

    async def test_malformed_lines_skipped(
        self,
        mock_docker: MagicMock,
        default_config: ContainerLogShippingConfig,
    ) -> None:
        lines = [
            '{"level": "info", "msg": "good"}\n',
            "not valid json\n",
            '{"level": "warn", "msg": "also good"}\n',
        ]
        _set_mock_log_output(mock_docker, lines)

        result = await collect_sidecar_logs(
            mock_docker,
            "sidecar123",
            config=default_config,
        )
        assert len(result) == 2
        assert result[0]["msg"] == "good"
        assert result[1]["msg"] == "also good"

    async def test_empty_logs(
        self,
        mock_docker: MagicMock,
        default_config: ContainerLogShippingConfig,
    ) -> None:
        _set_mock_log_output(mock_docker, [])
        result = await collect_sidecar_logs(
            mock_docker,
            "sidecar123",
            config=default_config,
        )
        assert result == ()

    @pytest.mark.parametrize(
        ("side_effect", "label"),
        [
            (TimeoutError, "timeout"),
            (RuntimeError("docker gone"), "generic error"),
        ],
        ids=["timeout", "generic-error"],
    )
    async def test_failure_returns_empty_tuple(
        self,
        mock_docker: MagicMock,
        default_config: ContainerLogShippingConfig,
        side_effect: Exception,
        label: str,
    ) -> None:
        container = MagicMock()
        container.log = AsyncMock(side_effect=side_effect)
        mock_docker.containers.container = MagicMock(return_value=container)

        result = await collect_sidecar_logs(
            mock_docker,
            "sidecar123",
            config=default_config,
        )
        assert result == (), f"Expected empty on {label}"

    async def test_max_log_bytes_truncation(
        self,
        mock_docker: MagicMock,
    ) -> None:
        lines = _make_log_lines(
            {"msg": "a" * 30},
            {"msg": "b" * 30},
            {"msg": "c" * 30},
        )
        _set_mock_log_output(mock_docker, lines)

        config = ContainerLogShippingConfig(max_log_bytes=60)
        result = await collect_sidecar_logs(
            mock_docker,
            "sidecar123",
            config=config,
        )
        assert len(result) == 1


# ── ship_container_logs ─────────────────────────────────────────


_SHIP_KWARGS: dict[str, Any] = {
    "container_id": "abc123def456",
    "sidecar_id": "side78901234",
    "stdout": "hello world",
    "stderr": "",
    "sidecar_logs": ({"msg": "sidecar event"},),
    "execution_time_ms": 1200,
}


class TestShipContainerLogs:
    """Tests for the standalone ship_container_logs function."""

    async def test_metadata_shipped_by_default(
        self,
        default_config: ContainerLogShippingConfig,
    ) -> None:
        with structlog.testing.capture_logs() as cap:
            await ship_container_logs(config=default_config, **_SHIP_KWARGS)

        shipped = [e for e in cap if e["event"] == SANDBOX_CONTAINER_LOGS_SHIPPED]
        assert len(shipped) == 1
        evt = shipped[0]
        assert evt["container_id"] == "abc123def456"
        assert evt["sidecar_id"] == "side78901234"
        assert evt["execution_time_ms"] == 1200
        assert evt["stdout_size"] == len("hello world")
        assert evt["stderr_size"] == 0
        assert evt["sidecar_log_count"] == 1
        # Raw bodies NOT present by default (ship_raw_logs=False).
        assert "stdout" not in evt
        assert "sidecar_logs" not in evt

    async def test_raw_logs_shipped_when_opted_in(self) -> None:
        config = ContainerLogShippingConfig(ship_raw_logs=True)
        with structlog.testing.capture_logs() as cap:
            await ship_container_logs(config=config, **_SHIP_KWARGS)

        shipped = [e for e in cap if e["event"] == SANDBOX_CONTAINER_LOGS_SHIPPED]
        assert len(shipped) == 1
        evt = shipped[0]
        assert evt["stdout"] == "hello world"
        assert evt["sidecar_logs"] == ({"msg": "sidecar event"},)

    async def test_disabled_config_skips_shipping(self) -> None:
        config = ContainerLogShippingConfig(enabled=False)
        with structlog.testing.capture_logs() as cap:
            await ship_container_logs(
                config=config,
                container_id="abc123",
                sidecar_id=None,
                stdout="output",
                stderr="err",
                sidecar_logs=(),
                execution_time_ms=100,
            )
        shipped = [e for e in cap if e["event"] == SANDBOX_CONTAINER_LOGS_SHIPPED]
        assert len(shipped) == 0

    async def test_failure_does_not_raise(self) -> None:
        config = ContainerLogShippingConfig()
        with patch(f"{_SHIPPER_MODULE}.logger") as mock_logger:
            mock_logger.info.side_effect = RuntimeError("logging broken")
            mock_logger.debug = MagicMock()

            await ship_container_logs(
                config=config,
                container_id="abc123",
                sidecar_id=None,
                stdout="output",
                stderr="",
                sidecar_logs=(),
                execution_time_ms=100,
            )

            mock_logger.debug.assert_called_once_with(
                SANDBOX_CONTAINER_LOGS_SHIP_FAILED,
                container_id="abc123",
                error_type="RuntimeError",
                error="RuntimeError: logging broken",
            )

    async def test_shared_byte_budget_truncation(self) -> None:
        config = ContainerLogShippingConfig(
            max_log_bytes=100,
            ship_raw_logs=True,
        )
        with structlog.testing.capture_logs() as cap:
            await ship_container_logs(
                config=config,
                container_id="abc123",
                sidecar_id=None,
                stdout="x" * 500,
                stderr="y" * 500,
                sidecar_logs=(),
                execution_time_ms=100,
            )

        shipped = [e for e in cap if e["event"] == SANDBOX_CONTAINER_LOGS_SHIPPED]
        assert len(shipped) == 1
        evt = shipped[0]
        # stdout gets full 100 budget; stderr gets 0 remainder.
        assert 0 < len(evt["stdout"]) <= 100
        assert evt["stderr"] == ""
        assert evt["stdout_size"] == 500
        assert evt["stderr_size"] == 500

    @pytest.mark.parametrize(
        "sidecar_id",
        ["side78901234", None],
        ids=["with-sidecar", "no-sidecar"],
    )
    async def test_sidecar_id_handling(
        self,
        default_config: ContainerLogShippingConfig,
        sidecar_id: str | None,
    ) -> None:
        with structlog.testing.capture_logs() as cap:
            await ship_container_logs(
                config=default_config,
                container_id="abc123",
                sidecar_id=sidecar_id,
                stdout="out",
                stderr="",
                sidecar_logs=(),
                execution_time_ms=50,
            )

        shipped = [e for e in cap if e["event"] == SANDBOX_CONTAINER_LOGS_SHIPPED]
        assert len(shipped) == 1
        if sidecar_id is None:
            assert shipped[0]["sidecar_id"] is None
        else:
            assert shipped[0]["sidecar_id"] == sidecar_id[:12]


# ── build_correlation_env ───────────────────────────────────────


class TestBuildCorrelationEnv:
    """Tests for SYNTHORG_* env var construction from contextvars."""

    def test_all_vars_injected_from_contextvars(self) -> None:
        with patch(
            "structlog.contextvars.get_contextvars",
            return_value={
                "agent_id": "agent-ceo",
                "session_id": "sess-1",
                "task_id": "task-42",
                "request_id": "req-abc",
            },
        ):
            env_list = build_correlation_env()

        env_dict = _env_to_dict(env_list)
        assert env_dict["SYNTHORG_AGENT_ID"] == "agent-ceo"
        assert env_dict["SYNTHORG_SESSION_ID"] == "sess-1"
        assert env_dict["SYNTHORG_TASK_ID"] == "task-42"
        assert env_dict["SYNTHORG_REQUEST_ID"] == "req-abc"

    def test_missing_contextvars_yield_empty_strings(self) -> None:
        with patch(
            "structlog.contextvars.get_contextvars",
            return_value={},
        ):
            env_list = build_correlation_env()

        env_dict = _env_to_dict(env_list)
        assert env_dict["SYNTHORG_AGENT_ID"] == ""
        assert env_dict["SYNTHORG_SESSION_ID"] == ""
        assert env_dict["SYNTHORG_TASK_ID"] == ""
        assert env_dict["SYNTHORG_REQUEST_ID"] == ""


# ── DockerSandbox integration ───────────────────────────────────


class TestDockerSandboxEnvIntegration:
    """DockerSandbox._validate_env and correlation env merge tests."""

    def test_synthorg_prefix_not_blocked_by_reserved_keys(
        self,
        sandbox: DockerSandbox,
    ) -> None:
        env_list = sandbox._validate_env(
            {"SYNTHORG_AGENT_ID": "agent-ceo", "MY_VAR": "val"},
        )
        env_dict = _env_to_dict(env_list)
        assert "SYNTHORG_AGENT_ID" in env_dict
        assert "MY_VAR" in env_dict

    def test_reserved_sidecar_keys_still_blocked(
        self,
        sandbox: DockerSandbox,
    ) -> None:
        from synthorg.tools.sandbox.errors import SandboxError

        with pytest.raises(SandboxError, match="reserved"):
            sandbox._validate_env({"SIDECAR_ALLOWED_HOSTS": "evil"})
