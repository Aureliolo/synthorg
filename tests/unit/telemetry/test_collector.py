"""Tests for the telemetry collector."""

import os
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import structlog.testing

from synthorg.telemetry.collector import (
    TelemetryCollector,
    _HeartbeatParams,
    _SessionSummaryParams,
)
from synthorg.telemetry.config import TelemetryBackend, TelemetryConfig
from synthorg.telemetry.protocol import TelemetryEvent


@pytest.fixture(autouse=True)
def clear_synthorg_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure the SYNTHORG_TELEMETRY* vars + well-known CI markers are unset.

    The collector reads a four-level env chain on construction; any
    leaking env var would make test behaviour depend on the shell it
    runs in. Scrub the whole set so each test specifies exactly the
    inputs it exercises.
    """
    monkeypatch.delenv("SYNTHORG_TELEMETRY_ENABLED", raising=False)
    monkeypatch.delenv("SYNTHORG_TELEMETRY_ENV", raising=False)
    monkeypatch.delenv("SYNTHORG_TELEMETRY_ENV_BAKED", raising=False)
    for marker in ("CI", "GITLAB_CI", "BUILDKITE", "JENKINS_URL"):
        monkeypatch.delenv(marker, raising=False)
    for name in list(os.environ):
        if name.startswith("RUNPOD_"):
            monkeypatch.delenv(name, raising=False)


@pytest.mark.unit
class TestTelemetryCollector:
    """TelemetryCollector unit tests."""

    def test_disabled_by_default(self, tmp_path: Path) -> None:
        config = TelemetryConfig()
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        assert collector.enabled is False
        assert collector.is_functional is False
        assert collector.deployment_id is None

    async def test_disabled_emits_one_info_at_startup_no_heartbeat(
        self,
        tmp_path: Path,
    ) -> None:
        """Issue #1666 A-3: disabled state -> one INFO, zero report_failed.

        Nothing should ever fire ``telemetry.report.failed`` on the
        disabled path -- the heartbeat task isn't scheduled, so no
        per-cycle warnings can ever land in the main log.
        """
        config = TelemetryConfig(enabled=False)
        with structlog.testing.capture_logs() as logs:
            collector = TelemetryCollector(config=config, data_dir=tmp_path)
            try:
                await collector.start()
            finally:
                await collector.shutdown()

        info_lines = [
            log
            for log in logs
            if log.get("event") == "telemetry.disabled"
            and log.get("log_level") == "info"
        ]
        assert len(info_lines) == 1
        report_failed = [
            log for log in logs if log.get("event") == "telemetry.report.failed"
        ]
        assert report_failed == []
        # Heartbeat task never scheduled when disabled.
        assert collector._heartbeat_task is None

    async def test_enabled_logfire_with_sentinel_token_emits_error_no_heartbeat(
        self,
        tmp_path: Path,
    ) -> None:
        """Issue #1666 A-3: enabled + sentinel token -> ONE ERROR, no heartbeat.

        With telemetry enabled but the build artifact shipping the
        sentinel token, the collector must log a single
        ``telemetry.token.missing`` ERROR at startup and skip the
        heartbeat scheduling so the per-cycle warning spam is
        eliminated.
        """
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.LOGFIRE)
        # Default sentinel is_token_embedded() == False in source-tree
        # installs (and in this CI environment); no monkeypatch needed.
        with structlog.testing.capture_logs() as logs:
            collector = TelemetryCollector(config=config, data_dir=tmp_path)
            try:
                await collector.start()
            finally:
                await collector.shutdown()

        token_missing = [
            log
            for log in logs
            if log.get("event") == "telemetry.token.missing"
            and log.get("log_level") == "error"
        ]
        assert len(token_missing) == 1
        # Heartbeat task never scheduled when token is sentinel.
        assert collector._heartbeat_task is None

    async def test_enabled_noop_backend_skips_token_check(
        self,
        tmp_path: Path,
    ) -> None:
        """NOOP backend bypasses the LOGFIRE-only token-embedded gate.

        Operators who explicitly opt out of Logfire (or tests using
        the noop reporter) must not see ``telemetry.token.missing``
        -- the check is scoped to ``TelemetryBackend.LOGFIRE``.
        """
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        with structlog.testing.capture_logs() as logs:
            collector = TelemetryCollector(config=config, data_dir=tmp_path)
            try:
                await collector.start()
            finally:
                await collector.shutdown()

        token_missing = [
            log for log in logs if log.get("event") == "telemetry.token.missing"
        ]
        assert token_missing == []

    def test_is_functional_false_when_reporter_is_noop(
        self,
        tmp_path: Path,
    ) -> None:
        """Opted in + noop reporter collapses to ``is_functional=False``.

        Covers the "enabled in config but reporter degraded to noop"
        case that the health endpoint used to mis-report as enabled.
        """
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        assert collector.enabled is True
        assert collector.is_functional is False

    def test_is_functional_true_when_logfire_reporter_built(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Functional is True when opted in and a non-noop reporter is wired.

        ``logfire.configure`` is patched out to stop the SDK from
        spawning its ``check_logfire_token`` background thread,
        which would hit the real Logfire API with the dummy token
        and surface a thread-level 401 as a spurious test failure
        against an unrelated test in the full-suite run.
        """
        pytest.importorskip(
            "logfire",
            reason="logfire extra not installed in this environment",
        )
        import logfire as real_logfire

        monkeypatch.setenv(
            "SYNTHORG_LOGFIRE_PROJECT_TOKEN",
            "pylf_v1_test_000000000000000000000000000000000000000000",
        )
        config = TelemetryConfig(
            enabled=True,
            backend=TelemetryBackend.LOGFIRE,
        )
        with patch.object(real_logfire, "configure"):
            collector = TelemetryCollector(config=config, data_dir=tmp_path)
        assert collector.is_functional is True

    async def test_generates_deployment_id(self, tmp_path: Path) -> None:
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        try:
            await collector.start()
            assert collector.deployment_id is not None
            assert len(collector.deployment_id) == 36  # UUID4 with hyphens
        finally:
            await collector.shutdown()

    async def test_persists_deployment_id(self, tmp_path: Path) -> None:
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        c1 = TelemetryCollector(config=config, data_dir=tmp_path)
        c2 = TelemetryCollector(config=config, data_dir=tmp_path)
        try:
            await c1.start()
            await c2.start()
            assert c1.deployment_id == c2.deployment_id
        finally:
            await c1.shutdown()
            await c2.shutdown()

    async def test_deployment_id_file_created(self, tmp_path: Path) -> None:
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        try:
            await collector.start()
            id_file = tmp_path / "telemetry_id"
            assert id_file.exists()
            assert (
                id_file.read_text(encoding="utf-8").strip() == collector.deployment_id
            )
        finally:
            await collector.shutdown()

    async def test_deployment_id_read_error_generates_new(self, tmp_path: Path) -> None:
        """OSError on read falls back to generating a new ID.

        Patches :func:`os.path.exists` (the post-sanitiser read
        probe) instead of ``Path.exists``; ``_load_or_create_deployment_id``
        uses the ``os`` / builtin I/O pair so the CodeQL
        path-injection sanitiser sits on the same lines as the
        filesystem sinks. Matching the sanitised form
        (``normcase``+``normpath``) makes the patch cross-platform.
        """
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        # Pure string normalisation, no I/O. Mirrors the production
        # sanitiser so the patch's match string lines up with the
        # path the collector hands to ``os.path.exists``.
        deployment_file_str = os.path.normcase(
            os.path.normpath(str(tmp_path / "telemetry_id")),  # noqa: ASYNC240
        )
        read_error = OSError("permission denied")
        original_exists = os.path.exists

        def exists_side_effect(path: str) -> bool:
            if os.path.normcase(os.path.normpath(path)) == deployment_file_str:
                raise read_error
            return original_exists(path)

        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        try:
            with patch(
                "synthorg.telemetry.collector.os.path.exists",
                side_effect=exists_side_effect,
            ):
                await collector.start()
            assert collector.deployment_id is not None
            assert len(collector.deployment_id) == 36  # UUID4 with hyphens
        finally:
            await collector.shutdown()

    async def test_deployment_id_write_error_still_returns(
        self, tmp_path: Path
    ) -> None:
        """OSError on the atomic create still returns the generated ID.

        Patches :func:`os.open` (the ``O_CREAT | O_EXCL`` sink used
        by the atomic write path) instead of ``Path.write_text``;
        the collector now wins-or-loses the write race with
        :func:`os.open` so the sanitiser and sink stay textually
        adjacent for CodeQL and the ID survives a concurrent peer.
        """
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        # Pure string normalisation, no I/O.
        deployment_file_str = os.path.normcase(
            os.path.normpath(str(tmp_path / "telemetry_id")),  # noqa: ASYNC240
        )
        write_error = OSError("disk full")
        original_os_open = os.open

        def os_open_side_effect(
            path: str,
            flags: int,
            mode: int = 0o777,
        ) -> int:
            if (
                os.path.normcase(os.path.normpath(path)) == deployment_file_str
                and flags & os.O_EXCL
            ):
                raise write_error
            return original_os_open(path, flags, mode)

        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        try:
            with patch(
                "synthorg.telemetry.collector.os.open",
                side_effect=os_open_side_effect,
            ):
                await collector.start()
            assert collector.deployment_id is not None
            assert len(collector.deployment_id) == 36  # UUID4 with hyphens
        finally:
            await collector.shutdown()

    async def test_deployment_id_concurrent_peer_wins_race(
        self, tmp_path: Path
    ) -> None:
        """``FileExistsError`` on atomic create reuses the peer's UUID.

        Regression guard for the TOCTOU race: two replicas racing
        against the same ``/data`` volume must converge on one
        deployment ID instead of each clobbering the other. Simulated
        by making ``os.open`` with ``O_CREAT|O_EXCL`` raise
        ``FileExistsError`` while the peer file's UUID is pre-written
        on disk -- the collector reads that and uses it.
        """
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        deployment_file = tmp_path / "telemetry_id"
        # Pure string normalisation, no I/O.
        deployment_file_str = os.path.normcase(
            os.path.normpath(str(deployment_file)),  # noqa: ASYNC240
        )
        peer_uuid = "12345678-1234-5678-1234-567812345678"
        original_os_open = os.open

        def os_open_side_effect(
            path: str,
            flags: int,
            mode: int = 0o777,
        ) -> int:
            if (
                os.path.normcase(os.path.normpath(path)) == deployment_file_str
                and flags & os.O_EXCL
            ):
                # Peer wrote between our ``exists`` check and ``os.open``.
                deployment_file.write_text(peer_uuid, encoding="utf-8")
                raise FileExistsError(17, "File exists", path)
            return original_os_open(path, flags, mode)

        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        try:
            with patch(
                "synthorg.telemetry.collector.os.open",
                side_effect=os_open_side_effect,
            ):
                await collector.start()
            assert collector.deployment_id == peer_uuid
        finally:
            await collector.shutdown()

    async def test_send_heartbeat_disabled(self, tmp_path: Path) -> None:
        """Heartbeat should be a no-op when disabled."""
        config = TelemetryConfig(enabled=False)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        await collector.send_heartbeat(
            _HeartbeatParams(agent_count=5),
        )

    async def test_send_heartbeat_enabled_noop(self, tmp_path: Path) -> None:
        """Heartbeat with noop backend should succeed silently."""
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        try:
            await collector.start()
            await collector.send_heartbeat(
                _HeartbeatParams(
                    agent_count=5,
                    department_count=3,
                    template_name="startup",
                ),
            )
        finally:
            await collector.shutdown()

    async def test_send_session_summary_noop(self, tmp_path: Path) -> None:
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        try:
            await collector.start()
            await collector.send_session_summary(
                _SessionSummaryParams(
                    tasks_created=10,
                    tasks_completed=8,
                    tasks_failed=2,
                    provider_count=2,
                ),
            )
        finally:
            await collector.shutdown()

    async def test_start_and_shutdown(self, tmp_path: Path) -> None:
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        await collector.start()
        assert collector._heartbeat_task is not None
        await collector.shutdown()
        assert collector._heartbeat_task is None

    async def test_start_disabled_no_task(self, tmp_path: Path) -> None:
        config = TelemetryConfig(enabled=False)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        await collector.start()
        assert collector._heartbeat_task is None
        await collector.shutdown()


@pytest.mark.unit
class TestTelemetryCollectorWithMockReporter:
    """Collector tests with a mock reporter to verify event content."""

    async def test_heartbeat_event_structure(self, tmp_path: Path) -> None:
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)

        # Bypass the lifecycle so the mock reporter sees only the
        # heartbeat call this test asserts on (not an extra startup
        # event from ``start()``). The deployment-id load is the
        # only side effect ``start()`` performs that ``_build_event``
        # depends on; setting it directly is the equivalent stub.
        collector._deployment_id = "00000000-0000-4000-8000-000000000001"

        mock_reporter = AsyncMock()
        collector._reporter = mock_reporter

        await collector.send_heartbeat(
            _HeartbeatParams(
                agent_count=5,
                department_count=3,
                team_count=1,
                template_name="startup",
                persistence_backend="sqlite",
                memory_backend="mem0",
                features_enabled="meeting",
            ),
        )

        mock_reporter.report.assert_awaited_once()
        event: TelemetryEvent = mock_reporter.report.call_args[0][0]
        assert event.event_type == "deployment.heartbeat"
        assert event.deployment_id == collector.deployment_id
        assert event.properties["agent_count"] == 5
        assert event.properties["department_count"] == 3
        assert event.properties["template_name"] == "startup"
        assert "uptime_hours" in event.properties
        assert isinstance(event.timestamp, datetime)

    async def test_session_summary_event_structure(self, tmp_path: Path) -> None:
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        collector._deployment_id = "00000000-0000-4000-8000-000000000001"

        mock_reporter = AsyncMock()
        collector._reporter = mock_reporter

        await collector.send_session_summary(
            _SessionSummaryParams(
                tasks_created=10,
                tasks_completed=8,
                tasks_failed=2,
                error_rate_limit=1,
                provider_count=2,
                meetings_held=3,
            ),
        )

        mock_reporter.report.assert_awaited_once()
        event: TelemetryEvent = mock_reporter.report.call_args[0][0]
        assert event.event_type == "deployment.session_summary"
        assert event.properties["tasks_created"] == 10
        assert event.properties["tasks_completed"] == 8
        assert event.properties["meetings_held"] == 3

    async def test_reporter_exception_is_caught(self, tmp_path: Path) -> None:
        """Reporter raising Exception should not crash the collector."""
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        collector._deployment_id = "00000000-0000-4000-8000-000000000001"

        mock_reporter = AsyncMock()
        mock_reporter.report.side_effect = RuntimeError("network down")
        collector._reporter = mock_reporter

        # Should not raise.
        await collector.send_heartbeat(
            _HeartbeatParams(agent_count=1),
        )

        mock_reporter.report.assert_awaited_once()

    async def test_reporter_exception_does_not_block_subsequent(
        self, tmp_path: Path
    ) -> None:
        """After reporter failure, next send still works."""
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        collector._deployment_id = "00000000-0000-4000-8000-000000000001"

        mock_reporter = AsyncMock()
        mock_reporter.report.side_effect = [
            RuntimeError("fail"),
            None,
        ]
        collector._reporter = mock_reporter

        await collector.send_heartbeat()
        await collector.send_heartbeat()

        assert mock_reporter.report.await_count == 2


@pytest.mark.unit
class TestHeartbeatParams:
    """Frozen dataclass parameter bundles."""

    def test_defaults(self) -> None:
        p = _HeartbeatParams()
        assert p.agent_count == 0
        assert p.template_name == ""

    def test_frozen(self) -> None:
        p = _HeartbeatParams(agent_count=5)
        with pytest.raises(AttributeError, match="cannot assign"):
            p.agent_count = 10  # type: ignore[misc]


@pytest.mark.unit
class TestSessionSummaryParams:
    """Frozen dataclass parameter bundles."""

    def test_defaults(self) -> None:
        p = _SessionSummaryParams()
        assert p.tasks_created == 0
        assert p.provider_count == 0

    def test_frozen(self) -> None:
        p = _SessionSummaryParams(tasks_created=5)
        with pytest.raises(AttributeError, match="cannot assign"):
            p.tasks_created = 10  # type: ignore[misc]


@pytest.mark.unit
class TestResolveEnvironment:
    """Four-level priority chain for deployment-environment resolution."""

    def test_config_value_used_when_no_env(self) -> None:
        from synthorg.telemetry.collector import _resolve_environment

        assert _resolve_environment("dev", environ={}) == "dev"

    def test_operator_override_beats_everything(self) -> None:
        from synthorg.telemetry.collector import _resolve_environment

        env = {
            "SYNTHORG_TELEMETRY_ENV": "staging",
            "SYNTHORG_TELEMETRY_ENV_BAKED": "prod",
            "CI": "true",
        }
        assert _resolve_environment("dev", environ=env) == "staging"

    @pytest.mark.parametrize(
        "marker",
        ["CI", "GITLAB_CI", "BUILDKITE", "JENKINS_URL"],
    )
    def test_ci_marker_sets_ci(self, marker: str) -> None:
        from synthorg.telemetry.collector import _resolve_environment

        env = {marker: "true", "SYNTHORG_TELEMETRY_ENV_BAKED": "prod"}
        assert _resolve_environment("dev", environ=env) == "ci"

    def test_runpod_prefix_marker_sets_ci(self) -> None:
        from synthorg.telemetry.collector import _resolve_environment

        env = {"RUNPOD_POD_ID": "abc123"}
        assert _resolve_environment("dev", environ=env) == "ci"

    def test_baked_value_used_when_no_ci_or_override(self) -> None:
        from synthorg.telemetry.collector import _resolve_environment

        env = {"SYNTHORG_TELEMETRY_ENV_BAKED": "pre-release"}
        assert _resolve_environment("dev", environ=env) == "pre-release"

    def test_ci_beats_baked_value(self) -> None:
        """Running a pre-release / prod image under CI tags as ci."""
        from synthorg.telemetry.collector import _resolve_environment

        env = {
            "CI": "true",
            "SYNTHORG_TELEMETRY_ENV_BAKED": "prod",
        }
        assert _resolve_environment("dev", environ=env) == "ci"

    def test_whitespace_values_ignored(self) -> None:
        from synthorg.telemetry.collector import _resolve_environment

        env = {"SYNTHORG_TELEMETRY_ENV": "   ", "SYNTHORG_TELEMETRY_ENV_BAKED": ""}
        assert _resolve_environment("dev", environ=env) == "dev"

    def test_override_truncated_to_64_chars(self) -> None:
        from synthorg.telemetry.collector import _resolve_environment

        env = {"SYNTHORG_TELEMETRY_ENV": "x" * 100}
        result = _resolve_environment("dev", environ=env)
        assert len(result) == 64
        assert result == "x" * 64


@pytest.mark.unit
class TestCollectorEnvironmentPropagation:
    """End-to-end: constructor env resolution and event enrichment."""

    def test_ci_env_marker_overrides_config_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CI", "true")
        config = TelemetryConfig(
            enabled=True,
            backend=TelemetryBackend.NOOP,
            environment="dev",
        )
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        assert collector._config.environment == "ci"

    def test_baked_env_overrides_config_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SYNTHORG_TELEMETRY_ENV_BAKED", "prod")
        config = TelemetryConfig(
            enabled=True,
            backend=TelemetryBackend.NOOP,
            environment="dev",
        )
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        assert collector._config.environment == "prod"

    def test_operator_override_beats_ci(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CI", "true")
        monkeypatch.setenv("SYNTHORG_TELEMETRY_ENV", "staging")
        config = TelemetryConfig(
            enabled=True,
            backend=TelemetryBackend.NOOP,
            environment="dev",
        )
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        assert collector._config.environment == "staging"

    def test_built_event_carries_resolved_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SYNTHORG_TELEMETRY_ENV", "pre-release")
        config = TelemetryConfig(enabled=True, backend=TelemetryBackend.NOOP)
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        # Build event without going through the lifecycle: stub the
        # deployment-id directly because this test only asserts the
        # environment field that ``_build_event`` reads from config.
        collector._deployment_id = "00000000-0000-4000-8000-000000000001"
        event = collector._build_event("deployment.heartbeat")
        assert event.environment == "pre-release"

    async def test_startup_event_attaches_docker_info_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the docker socket is missing, startup ships the marker.

        Also verifies the event carries the resolved ``environment``
        field so a docker-info regression doesn't mask an
        environment-resolution regression.
        """
        monkeypatch.setattr(
            "synthorg.telemetry.host_info.os.path.exists",
            lambda _path: False,
        )
        config = TelemetryConfig(
            enabled=True,
            backend=TelemetryBackend.NOOP,
            environment="test-env",
        )
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        collector._deployment_id = "00000000-0000-4000-8000-000000000001"
        mock_reporter = AsyncMock()
        collector._reporter = mock_reporter

        await collector._send_startup_event()

        mock_reporter.report.assert_awaited_once()
        event = mock_reporter.report.call_args.args[0]
        assert event.event_type == "deployment.startup"
        assert event.environment == "test-env"
        assert event.properties["docker_info_available"] is False
        assert (
            event.properties["docker_info_unavailable_reason"] == "socket_not_mounted"
        )

    async def test_startup_event_survives_fetch_docker_info_regression(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Belt-and-suspenders: unexpected exception from the helper
        still ships the startup event with the daemon-unreachable marker.

        :func:`synthorg.telemetry.host_info.fetch_docker_info` is
        contracted to never raise, but a regression there must not
        abort the startup event. The outer ``try/except`` in
        :meth:`_send_startup_event` is the safety net; this test
        verifies it catches the exception, logs the categorical
        reason, and ships the event with the collapsed marker.
        """
        from unittest.mock import AsyncMock as _AsyncMock

        monkeypatch.setattr(
            "synthorg.telemetry.collector.fetch_docker_info",
            _AsyncMock(side_effect=RuntimeError("helper regression")),
        )
        config = TelemetryConfig(
            enabled=True,
            backend=TelemetryBackend.NOOP,
            environment="dev",
        )
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        collector._deployment_id = "00000000-0000-4000-8000-000000000001"
        mock_reporter = AsyncMock()
        collector._reporter = mock_reporter

        await collector._send_startup_event()

        mock_reporter.report.assert_awaited_once()
        event = mock_reporter.report.call_args.args[0]
        assert event.event_type == "deployment.startup"
        assert event.properties["docker_info_available"] is False
        assert (
            event.properties["docker_info_unavailable_reason"] == "daemon_unreachable"
        )

    async def test_startup_event_passes_privacy_scrubber_end_to_end(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Full flow: startup event with docker info passes scrubber validation."""
        from synthorg.telemetry.privacy import PrivacyScrubber

        monkeypatch.setattr(
            "synthorg.telemetry.host_info.os.path.exists",
            lambda _path: False,
        )
        config = TelemetryConfig(
            enabled=True,
            backend=TelemetryBackend.NOOP,
            environment="integration-test",
        )
        collector = TelemetryCollector(config=config, data_dir=tmp_path)
        collector._deployment_id = "00000000-0000-4000-8000-000000000001"
        mock_reporter = AsyncMock()
        collector._reporter = mock_reporter

        await collector._send_startup_event()

        event = mock_reporter.report.call_args.args[0]
        # The collector emits events through `_send`, which already
        # runs the scrubber. Running it again here locks in the
        # end-to-end contract: every field the collector sends on
        # startup is either on the allowlist or filtered out.
        PrivacyScrubber().validate(event)

    def test_looks_like_ci_uses_os_environ_when_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_looks_like_ci(None) falls back to os.environ -- covers
        the production call path where the collector passes None."""
        from synthorg.telemetry.collector import _looks_like_ci

        monkeypatch.setenv("CI", "true")
        assert _looks_like_ci(None) is True

        monkeypatch.delenv("CI", raising=False)
        assert _looks_like_ci(None) is False
