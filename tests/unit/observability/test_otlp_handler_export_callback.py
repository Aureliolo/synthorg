"""Tests for the OTLP log handler's export callback hook.

The handler's ``set_export_callback`` lets startup wiring bridge
export outcomes into the Prometheus collector without the handler
depending on AppState.
"""

import logging
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
import structlog
from structlog.typing import EventDict

from synthorg.observability.events.metrics import METRICS_OTLP_CALLBACK_ERROR
from synthorg.observability.otlp_handler import OtlpHandler

pytestmark = pytest.mark.unit


def _make_record(level: int = logging.INFO, message: str = "x") -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )


@pytest.fixture
def captured_logs() -> Iterator[list[EventDict]]:
    """Capture structlog events emitted by the handler's internal logger."""
    with structlog.testing.capture_logs() as cap:
        yield cap


@pytest.mark.parametrize(
    ("side_effect", "expected"),
    [
        (None, ("success", 0)),
        (OSError("boom"), ("failure", 1)),
    ],
)
def test_export_callback_outcomes(
    side_effect: Exception | None,
    expected: tuple[str, int],
) -> None:
    handler = OtlpHandler(
        endpoint="http://example.invalid:4318",
        _start_flusher=False,
    )
    callback = MagicMock()
    handler.set_export_callback(callback)

    if side_effect is None:
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__ = MagicMock(return_value=None)
            mock_open.return_value.__exit__ = MagicMock(return_value=None)
            handler._export_batch([_make_record()])
    else:
        with patch("urllib.request.urlopen", side_effect=side_effect):
            handler._export_batch([_make_record()])

    callback.assert_called_once_with(*expected)


def test_export_callback_exceptions_are_swallowed(
    captured_logs: list[EventDict],
) -> None:
    handler = OtlpHandler(
        endpoint="http://example.invalid:4318",
        _start_flusher=False,
    )

    def _bad_callback(_outcome: str, _dropped: int) -> None:
        error_msg = "callback bug"
        raise RuntimeError(error_msg)

    handler.set_export_callback(_bad_callback)

    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__ = MagicMock(return_value=None)
        mock_open.return_value.__exit__ = MagicMock(return_value=None)
        handler._export_batch([_make_record()])

    callback_errors = [
        entry
        for entry in captured_logs
        if entry.get("event") == METRICS_OTLP_CALLBACK_ERROR
    ]
    assert len(callback_errors) == 1
    assert callback_errors[0]["log_level"] == "warning"


def test_no_callback_is_noop() -> None:
    handler = OtlpHandler(
        endpoint="http://example.invalid:4318",
        _start_flusher=False,
    )
    # No callback registered -- export should still run cleanly.
    with patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value.__enter__ = MagicMock(return_value=None)
        mock_open.return_value.__exit__ = MagicMock(return_value=None)
        handler._export_batch([_make_record()])
    # The handler does not change state -- a registered callback
    # would have been invoked; its absence is the assertion.
    assert handler._export_callback is None


def test_set_export_callback_rejects_non_callable() -> None:
    handler = OtlpHandler(
        endpoint="http://example.invalid:4318",
        _start_flusher=False,
    )
    with pytest.raises(TypeError, match="callable or None"):
        handler.set_export_callback("not-a-callable")  # type: ignore[arg-type]
