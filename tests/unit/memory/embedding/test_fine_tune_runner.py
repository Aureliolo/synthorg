"""Tests for fine-tune pipeline container entrypoint."""

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.memory.embedding.fine_tune_runner import (
    _DEFAULT_HEALTH_PORT,
    _load_config,
    _run,
    resolve_health_port,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _mock_health_server() -> Iterator[None]:
    """Prevent real port binding in tests."""
    mock_server = MagicMock()
    with patch(
        "synthorg.memory.embedding.fine_tune_runner._start_health_server",
        return_value=mock_server,
    ):
        yield


class TestResolveHealthPort:
    """`resolve_health_port` env-driven port resolution."""

    _ENV_VAR = "SYNTHORG_FINE_TUNE_HEALTH_PORT"

    def test_env_unset_returns_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(self._ENV_VAR, raising=False)
        assert resolve_health_port() == _DEFAULT_HEALTH_PORT

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("20000", 20000),
            ("1", 1),
            ("65535", 65535),
        ],
        ids=["typical", "low_boundary", "high_boundary"],
    )
    def test_valid_env_returns_int(
        self,
        monkeypatch: pytest.MonkeyPatch,
        raw: str,
        expected: int,
    ) -> None:
        monkeypatch.setenv(self._ENV_VAR, raw)
        assert resolve_health_port() == expected

    @pytest.mark.parametrize(
        ("raw", "match"),
        [
            # Empty env var (common accidental container config) is
            # treated as an integer-parse failure, not a silent default.
            ("", "not a valid integer"),
            ("not-a-port", "not a valid integer"),
            ("-1", "out of range"),
            ("0", "out of range"),
            ("65536", "out of range"),
        ],
        ids=[
            "empty_string_raises",
            "non_integer_raises",
            "negative_raises",
            "zero_raises",
            "above_max_raises",
        ],
    )
    def test_invalid_env_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        raw: str,
        match: str,
    ) -> None:
        monkeypatch.setenv(self._ENV_VAR, raw)
        with pytest.raises(ValueError, match=match):
            resolve_health_port()


class TestLoadConfig:
    """Config file loading and validation."""

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        with patch(
            "synthorg.memory.embedding.fine_tune_runner._CONFIG_PATH",
            tmp_path / "nonexistent.json",
        ):
            assert _load_config() is None

    def test_invalid_json_returns_none(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "config.json"
        bad_file.write_text("{invalid", encoding="utf-8")
        with patch(
            "synthorg.memory.embedding.fine_tune_runner._CONFIG_PATH",
            bad_file,
        ):
            assert _load_config() is None

    def test_non_object_json_returns_none(self, tmp_path: Path) -> None:
        array_file = tmp_path / "config.json"
        array_file.write_text("[1, 2, 3]", encoding="utf-8")
        with patch(
            "synthorg.memory.embedding.fine_tune_runner._CONFIG_PATH",
            array_file,
        ):
            assert _load_config() is None

    def test_valid_config_returns_dict(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({"stage": "generating_data"}),
            encoding="utf-8",
        )
        with patch(
            "synthorg.memory.embedding.fine_tune_runner._CONFIG_PATH",
            config_file,
        ):
            result = _load_config()
            assert result == {"stage": "generating_data"}


class TestRun:
    """Entrypoint _run() error handling."""

    def test_invalid_env_port_fails_fast_at_entrypoint(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Malformed ``SYNTHORG_FINE_TUNE_HEALTH_PORT`` aborts ``_run()``.

        The other tests stub ``_start_health_server`` via the
        ``_mock_health_server`` autouse fixture. Here we route that
        stub through a lambda that re-invokes ``resolve_health_port``
        so the port validation still runs -- locking in the fast-fail
        container contract: a bad port must crash startup, not
        silently bind the wrong port.
        """
        monkeypatch.setenv("SYNTHORG_FINE_TUNE_HEALTH_PORT", "not-a-port")
        with (
            patch(
                "synthorg.memory.embedding.fine_tune_runner._start_health_server",
                side_effect=resolve_health_port,
            ),
            pytest.raises(ValueError, match="not a valid integer"),
        ):
            _run()

    def test_missing_config_returns_1(self, tmp_path: Path) -> None:
        with patch(
            "synthorg.memory.embedding.fine_tune_runner._CONFIG_PATH",
            tmp_path / "nonexistent.json",
        ):
            assert _run() == 1

    def test_unknown_stage_returns_1(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({"stage": "not_a_stage"}),
            encoding="utf-8",
        )
        with patch(
            "synthorg.memory.embedding.fine_tune_runner._CONFIG_PATH",
            config_file,
        ):
            assert _run() == 1

    def test_non_executable_stage_returns_1(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({"stage": "idle"}),
            encoding="utf-8",
        )
        with patch(
            "synthorg.memory.embedding.fine_tune_runner._CONFIG_PATH",
            config_file,
        ):
            assert _run() == 1

    def test_empty_stage_returns_1(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({"stage": ""}),
            encoding="utf-8",
        )
        with patch(
            "synthorg.memory.embedding.fine_tune_runner._CONFIG_PATH",
            config_file,
        ):
            assert _run() == 1

    def test_successful_stage_returns_0(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "stage": "generating_data",
                    "source_dir": "/data",
                    "output_dir": "/checkpoints",
                }
            ),
            encoding="utf-8",
        )
        mock_dispatch = AsyncMock()
        with (
            patch(
                "synthorg.memory.embedding.fine_tune_runner._CONFIG_PATH",
                config_file,
            ),
            patch(
                "synthorg.memory.embedding.fine_tune_runner._dispatch_stage",
                mock_dispatch,
            ),
        ):
            assert _run() == 0
        mock_dispatch.assert_awaited_once()
        captured = capsys.readouterr()
        assert "STAGE_START:generating_data" in captured.out
        assert "STAGE_COMPLETE:generating_data" in captured.out

    def test_stage_exception_returns_1(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({"stage": "generating_data"}),
            encoding="utf-8",
        )
        mock_dispatch = AsyncMock(side_effect=ValueError("bad config"))
        with (
            patch(
                "synthorg.memory.embedding.fine_tune_runner._CONFIG_PATH",
                config_file,
            ),
            patch(
                "synthorg.memory.embedding.fine_tune_runner._dispatch_stage",
                mock_dispatch,
            ),
        ):
            assert _run() == 1

    def test_memory_error_propagates(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({"stage": "generating_data"}),
            encoding="utf-8",
        )
        mock_dispatch = AsyncMock(side_effect=MemoryError("OOM"))
        with (
            patch(
                "synthorg.memory.embedding.fine_tune_runner._CONFIG_PATH",
                config_file,
            ),
            patch(
                "synthorg.memory.embedding.fine_tune_runner._dispatch_stage",
                mock_dispatch,
            ),
            pytest.raises(MemoryError),
        ):
            _run()

    def test_recursion_error_propagates(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({"stage": "generating_data"}),
            encoding="utf-8",
        )
        mock_dispatch = AsyncMock(side_effect=RecursionError())
        with (
            patch(
                "synthorg.memory.embedding.fine_tune_runner._CONFIG_PATH",
                config_file,
            ),
            patch(
                "synthorg.memory.embedding.fine_tune_runner._dispatch_stage",
                mock_dispatch,
            ),
            pytest.raises(RecursionError),
        ):
            _run()

    def test_missing_stage_key_returns_1(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({"not_stage": "value"}),
            encoding="utf-8",
        )
        with patch(
            "synthorg.memory.embedding.fine_tune_runner._CONFIG_PATH",
            config_file,
        ):
            assert _run() == 1
