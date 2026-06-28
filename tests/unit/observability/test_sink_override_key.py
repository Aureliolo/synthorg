"""Tests for ``sink_override_key`` override-map key derivation."""

import pytest

from synthorg.observability.config import SinkConfig
from synthorg.observability.enums import SinkType
from synthorg.observability.sink_config_builder import (
    CONSOLE_SINK_ID,
    sink_override_key,
)

pytestmark = pytest.mark.unit


class TestSinkOverrideKey:
    def test_console_sink_keys_on_sentinel(self) -> None:
        sink = SinkConfig(sink_type=SinkType.CONSOLE)
        assert sink_override_key(sink) == CONSOLE_SINK_ID

    def test_file_sink_keys_on_file_path(self) -> None:
        sink = SinkConfig(sink_type=SinkType.FILE, file_path="logs/app.log")
        assert sink_override_key(sink) == "logs/app.log"

    def test_non_console_sink_without_file_path_raises(self) -> None:
        # A syslog sink has no file_path; deriving an override key is undefined.
        sink = SinkConfig(sink_type=SinkType.SYSLOG, syslog_host="logs.example")
        with pytest.raises(ValueError, match="no file_path"):
            sink_override_key(sink)
