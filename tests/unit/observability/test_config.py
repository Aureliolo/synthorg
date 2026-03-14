"""Tests for observability configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.observability.config import (
    DEFAULT_SINKS,
    LogConfig,
    RotationConfig,
    SinkConfig,
)
from synthorg.observability.enums import LogLevel, RotationStrategy, SinkType

from .conftest import LogConfigFactory, RotationConfigFactory, SinkConfigFactory

pytestmark = pytest.mark.timeout(30)

# ── RotationConfig ─────────────────────────────────────────────────


@pytest.mark.unit
class TestRotationConfig:
    """Tests for RotationConfig defaults, validation, and immutability."""

    def test_defaults(self) -> None:
        cfg = RotationConfig()
        assert cfg.strategy == RotationStrategy.BUILTIN
        assert cfg.max_bytes == 10 * 1024 * 1024
        assert cfg.backup_count == 5

    def test_custom_values(self) -> None:
        cfg = RotationConfig(
            strategy=RotationStrategy.EXTERNAL,
            max_bytes=5_000_000,
            backup_count=3,
        )
        assert cfg.strategy == RotationStrategy.EXTERNAL
        assert cfg.max_bytes == 5_000_000
        assert cfg.backup_count == 3

    def test_max_bytes_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            RotationConfig(max_bytes=0)

    def test_max_bytes_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RotationConfig(max_bytes=-1)

    def test_backup_count_zero_accepted(self) -> None:
        cfg = RotationConfig(backup_count=0)
        assert cfg.backup_count == 0

    def test_backup_count_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RotationConfig(backup_count=-1)

    def test_frozen(self) -> None:
        cfg = RotationConfig()
        with pytest.raises(ValidationError):
            cfg.max_bytes = 999  # type: ignore[misc]

    def test_factory(self) -> None:
        cfg = RotationConfigFactory.build()
        assert isinstance(cfg, RotationConfig)

    def test_json_roundtrip(self) -> None:
        cfg = RotationConfig(
            strategy=RotationStrategy.EXTERNAL,
            max_bytes=1_000_000,
            backup_count=10,
        )
        restored = RotationConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg


# ── SinkConfig ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestSinkConfig:
    """Tests for SinkConfig defaults, validation, and immutability."""

    def test_console_sink_defaults(self) -> None:
        cfg = SinkConfig(sink_type=SinkType.CONSOLE)
        assert cfg.level == LogLevel.INFO
        assert cfg.file_path is None
        assert cfg.rotation is None
        assert cfg.json_format is True

    def test_file_sink_valid(self) -> None:
        cfg = SinkConfig(
            sink_type=SinkType.FILE,
            file_path="app.log",
            rotation=RotationConfig(),
        )
        assert cfg.file_path == "app.log"
        assert cfg.rotation is not None

    def test_file_sink_requires_path(self) -> None:
        with pytest.raises(ValidationError, match="file_path is required"):
            SinkConfig(sink_type=SinkType.FILE)

    def test_file_sink_rejects_whitespace_path(self) -> None:
        with pytest.raises(ValidationError, match="empty or whitespace-only"):
            SinkConfig(sink_type=SinkType.FILE, file_path="   ")

    def test_file_sink_rejects_empty_path(self) -> None:
        with pytest.raises(ValidationError, match="empty or whitespace-only"):
            SinkConfig(sink_type=SinkType.FILE, file_path="")

    def test_file_sink_rejects_path_traversal(self) -> None:
        with pytest.raises(ValidationError, match=r"must not contain '\.\.'"):
            SinkConfig(
                sink_type=SinkType.FILE,
                file_path="../../../etc/passwd",
            )

    def test_file_sink_rejects_embedded_path_traversal(self) -> None:
        with pytest.raises(ValidationError, match=r"must not contain '\.\.'"):
            SinkConfig(
                sink_type=SinkType.FILE,
                file_path="logs/../../etc/shadow.log",
            )

    def test_console_sink_rejects_file_path(self) -> None:
        with pytest.raises(ValidationError, match="file_path must be None"):
            SinkConfig(
                sink_type=SinkType.CONSOLE,
                file_path="ignored.log",
            )

    def test_console_sink_rejects_rotation(self) -> None:
        with pytest.raises(ValidationError, match="rotation must be None"):
            SinkConfig(
                sink_type=SinkType.CONSOLE,
                rotation=RotationConfig(),
            )

    def test_file_sink_rejects_absolute_path(self) -> None:
        with pytest.raises(ValidationError, match="file_path must be relative"):
            SinkConfig(
                sink_type=SinkType.FILE,
                file_path="/etc/passwd",
            )

    def test_custom_level(self) -> None:
        cfg = SinkConfig(sink_type=SinkType.CONSOLE, level=LogLevel.ERROR)
        assert cfg.level == LogLevel.ERROR

    def test_frozen(self) -> None:
        cfg = SinkConfig(sink_type=SinkType.CONSOLE)
        with pytest.raises(ValidationError):
            cfg.level = LogLevel.DEBUG  # type: ignore[misc]

    def test_factory(self) -> None:
        cfg = SinkConfigFactory.build()
        assert isinstance(cfg, SinkConfig)

    def test_json_roundtrip(self) -> None:
        cfg = SinkConfig(
            sink_type=SinkType.FILE,
            level=LogLevel.DEBUG,
            file_path="test.log",
            rotation=RotationConfig(),
            json_format=True,
        )
        restored = SinkConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg


# ── LogConfig ──────────────────────────────────────────────────────


def _console_sink() -> SinkConfig:
    return SinkConfig(sink_type=SinkType.CONSOLE, json_format=False)


@pytest.mark.unit
class TestLogConfig:
    """Tests for LogConfig defaults, validation, and immutability."""

    def test_defaults(self) -> None:
        cfg = LogConfig(sinks=(_console_sink(),))
        assert cfg.root_level == LogLevel.DEBUG
        assert cfg.logger_levels == ()
        assert cfg.enable_correlation is True
        assert cfg.log_dir == "logs"

    def test_custom_values(self) -> None:
        cfg = LogConfig(
            root_level=LogLevel.WARNING,
            logger_levels=(("synthorg.engine", LogLevel.DEBUG),),
            sinks=(_console_sink(),),
            enable_correlation=False,
            log_dir="custom_logs",
        )
        assert cfg.root_level == LogLevel.WARNING
        assert len(cfg.logger_levels) == 1
        assert cfg.enable_correlation is False
        assert cfg.log_dir == "custom_logs"

    def test_empty_sinks_rejected(self) -> None:
        with pytest.raises(ValidationError, match="At least one sink"):
            LogConfig(sinks=())

    def test_duplicate_logger_names_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate logger names"):
            LogConfig(
                sinks=(_console_sink(),),
                logger_levels=(
                    ("synthorg.engine", LogLevel.DEBUG),
                    ("synthorg.engine", LogLevel.INFO),
                ),
            )

    def test_duplicate_file_paths_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate file paths"):
            LogConfig(
                sinks=(
                    SinkConfig(
                        sink_type=SinkType.FILE,
                        file_path="same.log",
                        rotation=RotationConfig(),
                    ),
                    SinkConfig(
                        sink_type=SinkType.FILE,
                        file_path="same.log",
                        rotation=RotationConfig(),
                    ),
                ),
            )

    def test_blank_log_dir_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            LogConfig(sinks=(_console_sink(),), log_dir="   ")

    def test_log_dir_traversal_rejected(self) -> None:
        with pytest.raises(
            ValidationError, match=r"must not contain '\.\.' components"
        ):
            LogConfig(sinks=(_console_sink(),), log_dir="../../../tmp")

    @pytest.mark.parametrize(
        "absolute_dir",
        ["/var/log", "/opt/app/logs", "C:\\Logs"],
    )
    def test_absolute_log_dir_accepted(self, absolute_dir: str) -> None:
        cfg = LogConfig(sinks=(_console_sink(),), log_dir=absolute_dir)
        assert cfg.log_dir == absolute_dir

    def test_frozen(self) -> None:
        cfg = LogConfig(sinks=(_console_sink(),))
        with pytest.raises(ValidationError):
            cfg.log_dir = "other"  # type: ignore[misc]

    def test_factory(self) -> None:
        cfg = LogConfigFactory.build()
        assert isinstance(cfg, LogConfig)

    def test_json_roundtrip(self) -> None:
        cfg = LogConfig(
            sinks=(_console_sink(),),
            logger_levels=(("synthorg.core", LogLevel.WARNING),),
        )
        restored = LogConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg


# ── DEFAULT_SINKS ──────────────────────────────────────────────────


@pytest.mark.unit
class TestDefaultSinks:
    """Tests for the DEFAULT_SINKS constant."""

    def test_count(self) -> None:
        assert len(DEFAULT_SINKS) == 7

    def test_first_is_console(self) -> None:
        assert DEFAULT_SINKS[0].sink_type == SinkType.CONSOLE
        assert DEFAULT_SINKS[0].json_format is False

    def test_file_sinks_have_paths(self) -> None:
        for sink in DEFAULT_SINKS[1:]:
            assert sink.sink_type == SinkType.FILE
            assert sink.file_path is not None
            assert sink.rotation is not None

    def test_no_duplicate_file_paths(self) -> None:
        paths = [s.file_path for s in DEFAULT_SINKS if s.file_path is not None]
        assert len(paths) == len(set(paths))

    def test_valid_as_log_config(self) -> None:
        cfg = LogConfig(sinks=DEFAULT_SINKS)
        assert len(cfg.sinks) == 7
