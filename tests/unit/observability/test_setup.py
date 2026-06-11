"""Tests for logging system setup."""

import json
import logging
import sys
from typing import TYPE_CHECKING, TextIO, override

import pytest
import structlog

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

from synthorg.observability.config import DEFAULT_SINKS, LogConfig, SinkConfig
from synthorg.observability.correlation import bind_correlation_id
from synthorg.observability.enums import LogLevel, SinkType
from synthorg.observability.setup import (
    _DEFAULT_LOGGER_LEVELS,
    _THIRD_PARTY_LOGGER_LEVELS,
    _apply_console_level_override,
    _attach_handlers,
    _tame_third_party_loggers,
    configure_logging,
)


def _console_only_config() -> LogConfig:
    return LogConfig(
        sinks=(
            SinkConfig(
                sink_type=SinkType.CONSOLE,
                level=LogLevel.DEBUG,
                json_format=False,
            ),
        ),
    )


def _file_config(tmp_path: Path) -> LogConfig:
    return LogConfig(
        sinks=(
            SinkConfig(
                sink_type=SinkType.FILE,
                level=LogLevel.DEBUG,
                file_path="test.log",
                json_format=True,
            ),
        ),
        log_dir=str(tmp_path),
    )


@pytest.mark.unit
class TestConfigureLogging:
    """Tests for configure_logging function."""

    def test_default_config_creates_handlers(self) -> None:
        configure_logging()
        root = logging.getLogger()
        assert len(root.handlers) == len(DEFAULT_SINKS)

    def test_custom_config_creates_handlers(self) -> None:
        configure_logging(_console_only_config())
        root = logging.getLogger()
        assert len(root.handlers) == 1

    def test_idempotent_no_duplicate_handlers(self) -> None:
        configure_logging(_console_only_config())
        configure_logging(_console_only_config())
        root = logging.getLogger()
        assert len(root.handlers) == 1

    def test_root_logger_defaults_to_info(self) -> None:
        # Root logger defaults to INFO:
        # DEBUG on root leaked verbose payloads through HTTP sinks and
        # wasted bandwidth.  Operators set ``observability.root_level=debug``
        # explicitly when they need the full event stream.
        configure_logging(_console_only_config())
        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_custom_root_level_applied(self) -> None:
        config = LogConfig(
            sinks=(_console_only_config().sinks),
            root_level=LogLevel.WARNING,
        )
        configure_logging(config)
        root = logging.getLogger()
        assert root.level == logging.WARNING

    def test_default_logger_levels_applied(self) -> None:
        configure_logging(_console_only_config())
        for name, level in _DEFAULT_LOGGER_LEVELS:
            logger = logging.getLogger(name)
            assert logger.level == getattr(logging, level.value), (
                f"{name} expected {level.value}"
            )

    def test_config_overrides_default_levels(self) -> None:
        config = LogConfig(
            sinks=(
                SinkConfig(
                    sink_type=SinkType.CONSOLE,
                    level=LogLevel.DEBUG,
                    json_format=False,
                ),
            ),
            logger_levels=(("synthorg.engine", LogLevel.CRITICAL),),
        )
        configure_logging(config)
        logger = logging.getLogger("synthorg.engine")
        assert logger.level == logging.CRITICAL

    def test_none_config_uses_defaults(self) -> None:
        configure_logging(None)
        root = logging.getLogger()
        assert len(root.handlers) == len(DEFAULT_SINKS)


@pytest.mark.unit
class TestConfigureLoggingFileOutput:
    """Tests for file output from configure_logging."""

    def test_file_sink_creates_log_file(self, tmp_path: Path) -> None:
        configure_logging(_file_config(tmp_path))
        logger = logging.getLogger("test.file_output")
        logger.info("hello file")
        log_file = tmp_path / "test.log"
        assert log_file.exists()

    def test_json_output_is_parseable(self, tmp_path: Path) -> None:
        configure_logging(_file_config(tmp_path))
        logger = logging.getLogger("test.json_output")
        logger.info("json test message")
        log_file = tmp_path / "test.log"
        content = log_file.read_text().strip()
        assert content
        record = json.loads(content)
        assert record["event"] == "json test message"


@pytest.mark.unit
class TestStdlibBridge:
    """Tests for stdlib logging bridge (foreign log records)."""

    def test_stdlib_logger_captured(self, tmp_path: Path) -> None:
        configure_logging(_file_config(tmp_path))
        stdlib_logger = logging.getLogger("uvicorn.access")
        stdlib_logger.setLevel(logging.DEBUG)
        stdlib_logger.info("stdlib bridge test")
        log_file = tmp_path / "test.log"
        content = log_file.read_text().strip()
        assert content
        record = json.loads(content)
        assert record["event"] == "stdlib bridge test"


@pytest.mark.unit
class TestDefaultLoggerLevels:
    """Tests for the _DEFAULT_LOGGER_LEVELS constant."""

    def test_is_not_empty(self) -> None:
        assert len(_DEFAULT_LOGGER_LEVELS) >= 1

    def test_all_start_with_synthorg(self) -> None:
        for name, _ in _DEFAULT_LOGGER_LEVELS:
            assert name.startswith("synthorg."), name

    def test_all_levels_are_valid(self) -> None:
        for _, level in _DEFAULT_LOGGER_LEVELS:
            assert isinstance(level, LogLevel)


@pytest.mark.unit
class TestSanitizationPipeline:
    """E2E tests for sensitive field sanitization through the pipeline."""

    def test_sensitive_fields_redacted_in_log_file(self, tmp_path: Path) -> None:
        configure_logging(_file_config(tmp_path))
        logger = structlog.get_logger("test.sanitize")
        logger.info("login attempt", password="s3cret", user="alice")
        log_file = tmp_path / "test.log"
        content = log_file.read_text().strip()
        assert content
        record = json.loads(content)
        assert record["password"] == "**REDACTED**"
        assert record["user"] == "alice"


@pytest.mark.unit
class TestCorrelationPipeline:
    """E2E tests for correlation IDs appearing in log file output."""

    def test_correlation_id_in_log_output(self, tmp_path: Path) -> None:
        configure_logging(_file_config(tmp_path))
        bind_correlation_id(request_id="test-req-123")
        logger = structlog.get_logger("test.correlation")
        logger.info("correlated event")
        log_file = tmp_path / "test.log"
        content = log_file.read_text().strip()
        assert content
        record = json.loads(content)
        assert record["request_id"] == "test-req-123"


@pytest.mark.unit
class TestApplyConsoleLevelOverride:
    """Tests for _apply_console_level_override."""

    def test_no_env_var_returns_unchanged(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("SYNTHORG_LOG_LEVEL", raising=False)
        config = _console_only_config()
        result = _apply_console_level_override(config)
        assert result is config

    def test_valid_level_overrides_console_sink(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SYNTHORG_LOG_LEVEL", "warning")
        config = _console_only_config()
        result = _apply_console_level_override(config)
        assert result.sinks[0].level == LogLevel.WARNING

    def test_invalid_level_falls_back_to_info(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("SYNTHORG_LOG_LEVEL", "bogus")
        config = _console_only_config()
        result = _apply_console_level_override(config)
        assert result.sinks[0].level == LogLevel.INFO
        captured = capsys.readouterr()
        # The warning identifies the invalid value and the source
        # (``env``) so an operator can see whether the override came
        # from ``SYNTHORG_LOG_LEVEL`` or the YAML field.
        assert "Invalid console-level override" in captured.err
        assert "'bogus'" in captured.err
        assert "source=env" in captured.err

    def test_file_sinks_unaffected(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SYNTHORG_LOG_LEVEL", "error")
        config = LogConfig(
            sinks=(
                SinkConfig(
                    sink_type=SinkType.CONSOLE,
                    level=LogLevel.INFO,
                    json_format=False,
                ),
                SinkConfig(
                    sink_type=SinkType.FILE,
                    level=LogLevel.DEBUG,
                    file_path="test.log",
                    json_format=True,
                ),
            ),
        )
        result = _apply_console_level_override(config)
        assert result.sinks[0].level == LogLevel.ERROR
        assert result.sinks[1].level == LogLevel.DEBUG

    def test_no_console_sink_warns(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("SYNTHORG_LOG_LEVEL", "debug")
        config = LogConfig(
            sinks=(
                SinkConfig(
                    sink_type=SinkType.FILE,
                    level=LogLevel.INFO,
                    file_path="only-file.log",
                    json_format=True,
                ),
            ),
        )
        result = _apply_console_level_override(config)
        assert result.sinks[0].level == LogLevel.INFO
        captured = capsys.readouterr()
        assert "no CONSOLE sink found" in captured.err


@pytest.mark.unit
class TestCriticalSinkFailure:
    """Tests for critical sink failure enforcement in _attach_handlers."""

    def test_non_critical_sink_failure_skipped(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-critical sink failure is tolerated."""
        import synthorg.observability.setup as _setup

        config = LogConfig(
            sinks=(
                SinkConfig(
                    sink_type=SinkType.FILE,
                    level=LogLevel.DEBUG,
                    file_path="debug.log",
                    json_format=True,
                ),
            ),
        )

        def _boom(**_kwargs: object) -> None:
            msg = "disk full"
            raise OSError(msg)

        monkeypatch.setattr(_setup, "build_handler", _boom)
        root = logging.getLogger()
        initial_count = len(root.handlers)
        # Should not raise -- non-critical sink failures are skipped.
        _attach_handlers(config, root, [])
        assert len(root.handlers) == initial_count

    def test_critical_audit_sink_failure_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """audit.log failure raises RuntimeError."""
        import synthorg.observability.setup as _setup

        config = LogConfig(
            sinks=(
                SinkConfig(
                    sink_type=SinkType.FILE,
                    level=LogLevel.INFO,
                    file_path="audit.log",
                    json_format=True,
                ),
            ),
        )

        def _boom(**_kwargs: object) -> None:
            msg = "permission denied"
            raise OSError(msg)

        monkeypatch.setattr(_setup, "build_handler", _boom)
        root = logging.getLogger()
        with pytest.raises(RuntimeError, match=r"audit\.log"):
            _attach_handlers(config, root, [])

    def test_critical_access_sink_failure_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """access.log failure raises RuntimeError."""
        import synthorg.observability.setup as _setup

        config = LogConfig(
            sinks=(
                SinkConfig(
                    sink_type=SinkType.FILE,
                    level=LogLevel.INFO,
                    file_path="access.log",
                    json_format=True,
                ),
            ),
        )

        def _boom(**_kwargs: object) -> None:
            msg = "permission denied"
            raise OSError(msg)

        monkeypatch.setattr(_setup, "build_handler", _boom)
        root = logging.getLogger()
        with pytest.raises(RuntimeError, match=r"access\.log"):
            _attach_handlers(config, root, [])

    def test_critical_sink_failure_chains_original_cause(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """RuntimeError chains the original OS error."""
        import synthorg.observability.setup as _setup

        config = LogConfig(
            sinks=(
                SinkConfig(
                    sink_type=SinkType.FILE,
                    level=LogLevel.INFO,
                    file_path="audit.log",
                    json_format=True,
                ),
            ),
        )

        def _boom(**_kwargs: object) -> None:
            msg = "permission denied"
            raise OSError(msg)

        monkeypatch.setattr(_setup, "build_handler", _boom)
        root = logging.getLogger()
        with pytest.raises(RuntimeError) as exc_info:
            _attach_handlers(config, root, [])
        assert isinstance(exc_info.value.__cause__, OSError)


@pytest.mark.unit
class TestReconfigurationLogRouting:
    """Regression tests: log routing after configure_logging() is called twice.

    Reproduces the production scenario where _bootstrap_app_logging()
    reconfigures logging after an initial configuration, and
    module-level loggers must route to the new handlers.
    """

    def test_module_level_logger_routes_to_new_handlers(
        self,
        tmp_path: Path,
    ) -> None:
        """Logger created BEFORE config routes to handlers from the SECOND config."""
        from synthorg.observability import get_logger

        # Create logger before any configuration (simulates module-level).
        test_logger = get_logger("test.reconfigure.module_level")

        # First configuration with one file sink.
        first_config = LogConfig(
            sinks=(
                SinkConfig(
                    sink_type=SinkType.FILE,
                    level=LogLevel.DEBUG,
                    file_path="first.log",
                    json_format=True,
                ),
            ),
            log_dir=str(tmp_path),
        )
        configure_logging(first_config)

        # Second configuration with a different file sink.
        second_config = LogConfig(
            sinks=(
                SinkConfig(
                    sink_type=SinkType.FILE,
                    level=LogLevel.DEBUG,
                    file_path="second.log",
                    json_format=True,
                ),
            ),
            log_dir=str(tmp_path),
        )
        configure_logging(second_config)

        # Emit through the pre-existing logger.
        test_logger.info("post-reconfigure-event")

        for h in logging.getLogger().handlers:
            h.flush()

        second_log = tmp_path / "second.log"
        content = second_log.read_text().strip()
        assert content, "Expected log output in second.log"
        record = json.loads(content)
        assert record["event"] == "post-reconfigure-event"

    def test_reconfiguration_old_handlers_disconnected(
        self,
        tmp_path: Path,
    ) -> None:
        """Old file sink receives no events after reconfiguration."""
        from synthorg.observability import get_logger

        configure_logging(
            LogConfig(
                sinks=(
                    SinkConfig(
                        sink_type=SinkType.FILE,
                        level=LogLevel.DEBUG,
                        file_path="old.log",
                        json_format=True,
                    ),
                ),
                log_dir=str(tmp_path),
            ),
        )

        configure_logging(
            LogConfig(
                sinks=(
                    SinkConfig(
                        sink_type=SinkType.FILE,
                        level=LogLevel.DEBUG,
                        file_path="new.log",
                        json_format=True,
                    ),
                ),
                log_dir=str(tmp_path),
            ),
        )

        test_logger = get_logger("test.reconfigure.disconnect")
        test_logger.info("after-reconfig")

        for h in logging.getLogger().handlers:
            h.flush()

        old_log = tmp_path / "old.log"
        assert not old_log.exists() or old_log.read_text().strip() == ""

    def test_structlog_cache_disabled(self) -> None:
        """configure_logging must set cache_logger_on_first_use=False."""
        configure_logging(_console_only_config())
        cfg = structlog.get_config()
        assert cfg["cache_logger_on_first_use"] is False


@pytest.mark.unit
class TestTameThirdPartyLoggers:
    """Tests for _tame_third_party_loggers."""

    @pytest.fixture(autouse=True)
    def _reset_third_party_loggers(self) -> Iterator[None]:
        """Reset third-party logger state before and after each test."""

        def _reset() -> None:
            for name, level in _THIRD_PARTY_LOGGER_LEVELS:
                lg = logging.getLogger(name)
                for handler in lg.handlers[:]:
                    lg.removeHandler(handler)
                lg.setLevel(level.value)
                lg.propagate = True

        _reset()
        yield
        _reset()

    def test_clears_litellm_handlers(self) -> None:
        """LiteLLM's own StreamHandler is removed after taming."""
        lg = logging.getLogger("LiteLLM")
        lg.addHandler(logging.StreamHandler())
        assert len(lg.handlers) >= 1
        _tame_third_party_loggers()
        assert lg.handlers == []

    def test_clears_non_litellm_handlers(self) -> None:
        """Non-LiteLLM third-party handlers are also removed."""
        lg = logging.getLogger("httpx")
        lg.addHandler(logging.StreamHandler())
        assert len(lg.handlers) >= 1
        _tame_third_party_loggers()
        assert lg.handlers == []

    def test_clears_multiple_handlers_from_single_logger(self) -> None:
        """All handlers removed even when a logger has multiple."""
        lg = logging.getLogger("LiteLLM")
        lg.addHandler(logging.StreamHandler())
        lg.addHandler(logging.StreamHandler())
        lg.addHandler(logging.StreamHandler())
        assert len(lg.handlers) == 3
        _tame_third_party_loggers()
        assert lg.handlers == []

    def test_handler_close_failure_warns_to_stderr(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A handler whose close() raises warns to stderr but is still removed."""

        class _BadHandler(logging.StreamHandler[TextIO]):
            @override
            def close(self) -> None:
                msg = "close failed"
                raise RuntimeError(msg)

        lg = logging.getLogger("LiteLLM")
        lg.addHandler(_BadHandler())
        _tame_third_party_loggers()
        assert lg.handlers == []
        captured = capsys.readouterr()
        assert "WARNING: Failed to close third-party log handler" in captured.err

    def test_sets_level_to_warning(self) -> None:
        """Third-party loggers are set to WARNING."""
        for name, _ in _THIRD_PARTY_LOGGER_LEVELS:
            lg = logging.getLogger(name)
            lg.setLevel(logging.DEBUG)  # reset to DEBUG first
        _tame_third_party_loggers()
        for name, expected_level in _THIRD_PARTY_LOGGER_LEVELS:
            lg = logging.getLogger(name)
            assert lg.level == getattr(logging, expected_level.value), name

    def test_propagate_stays_true(self) -> None:
        """Third-party loggers propagate to root (our sinks)."""
        for name, _ in _THIRD_PARTY_LOGGER_LEVELS:
            logging.getLogger(name).propagate = False
        _tame_third_party_loggers()
        for name, _ in _THIRD_PARTY_LOGGER_LEVELS:
            lg = logging.getLogger(name)
            assert lg.propagate is True, name

    def test_litellm_set_verbose_disabled(self) -> None:
        """litellm.set_verbose is set to False."""
        import litellm

        litellm.set_verbose = True  # type: ignore[attr-defined]
        _tame_third_party_loggers()
        assert litellm.set_verbose is False  # type: ignore[attr-defined]

    def test_litellm_suppress_debug_info_enabled(self) -> None:
        """litellm.suppress_debug_info is set to True."""
        import litellm

        litellm.suppress_debug_info = False
        _tame_third_party_loggers()
        assert litellm.suppress_debug_info is True

    def test_idempotent(self) -> None:
        """Calling twice does not raise or duplicate state."""
        _tame_third_party_loggers()
        _tame_third_party_loggers()
        for name, expected_level in _THIRD_PARTY_LOGGER_LEVELS:
            lg = logging.getLogger(name)
            assert lg.handlers == []
            assert lg.level == getattr(logging, expected_level.value)

    def test_called_by_configure_logging(self) -> None:
        """configure_logging invokes third-party taming."""
        lg = logging.getLogger("LiteLLM")
        lg.addHandler(logging.StreamHandler())
        configure_logging(_console_only_config())
        assert lg.handlers == []

    def test_explicit_override_takes_precedence_over_taming(self) -> None:
        """config.logger_levels overrides taming's WARNING default."""
        config = LogConfig(
            sinks=(
                SinkConfig(
                    sink_type=SinkType.CONSOLE,
                    level=LogLevel.DEBUG,
                    json_format=False,
                ),
            ),
            logger_levels=(("httpx", LogLevel.INFO),),
        )
        configure_logging(config)
        lg = logging.getLogger("httpx")
        assert lg.level == logging.INFO
        assert lg.handlers == []

    def test_messages_still_reach_root_sinks(
        self,
        tmp_path: Path,
    ) -> None:
        """LiteLLM WARNING+ messages propagate to file sinks."""
        configure_logging(_file_config(tmp_path))
        lg = logging.getLogger("LiteLLM")
        lg.warning("test litellm warning")
        log_file = tmp_path / "test.log"
        lines = [ln for ln in log_file.read_text().strip().splitlines() if ln.strip()]
        assert lines
        records = [json.loads(ln) for ln in lines]
        assert any(r["event"] == "test litellm warning" for r in records)

    def test_debug_messages_suppressed(
        self,
        tmp_path: Path,
    ) -> None:
        """LiteLLM DEBUG messages do not reach file sinks."""
        configure_logging(_file_config(tmp_path))
        root = logging.getLogger()
        assert root.handlers, "Precondition: file sink must be attached"
        lg = logging.getLogger("LiteLLM")
        lg.debug("should not appear")
        log_file = tmp_path / "test.log"
        content = log_file.read_text().strip() if log_file.exists() else ""
        assert "should not appear" not in content

    def test_skips_litellm_when_not_imported(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Does not import litellm but still cleans up other loggers."""
        monkeypatch.delitem(sys.modules, "litellm", raising=False)
        lg = logging.getLogger("httpx")
        lg.addHandler(logging.StreamHandler())
        lg.setLevel(logging.DEBUG)
        _tame_third_party_loggers()
        assert sys.modules.get("litellm") is None
        assert lg.handlers == []
        assert lg.level == logging.WARNING


@pytest.mark.unit
class TestConfigureLoggingEnvOverride:
    """Tests for configure_logging with env var overrides."""

    def test_synthorg_log_level_applied_end_to_end(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """SYNTHORG_LOG_LEVEL env var takes effect through configure_logging."""
        monkeypatch.setenv("SYNTHORG_LOG_LEVEL", "warning")
        configure_logging(_console_only_config())
        root = logging.getLogger()
        # The console handler level should reflect the override.
        assert any(h.level == logging.WARNING for h in root.handlers)
