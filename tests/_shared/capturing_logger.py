"""Capturing structlog-shaped logger for log-record assertions.

``structlog`` does NOT route through stdlib ``logging``, so pytest's
``caplog`` fixture is blind to project logs. Tests that need to assert
on a specific log record's event name or structured kwargs must
substitute the module-level ``logger`` with a capturing double instead.

The supported pattern is::

    from tests._shared import CapturingErrorLogger


    def test_failure_path_logs_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
        from synthorg.some.module import some_function
        from synthorg.some import module as _mod

        capturing = CapturingErrorLogger()
        monkeypatch.setattr(_mod, "logger", capturing)

        with pytest.raises(SomeError):
            some_function(...)

        error_calls = [
            kwargs for event, kwargs in capturing.calls if event == "some.event.failed"
        ]
        assert error_calls, "expected the redacted-error log"
        assert "leaked-secret" not in str(error_calls[0]["error"])

The other severity methods are accepted but discarded so the production
helper's ``logger.debug`` / ``logger.info`` calls during the same flow
do not crash the test. Only ``error`` and ``warning`` records are
captured because those are the severities the SEC-1 redaction helpers
emit; extending the captured set is a one-line change if a future
test needs to assert on ``info``-level records too.
"""

from typing import Any


class CapturingErrorLogger:
    """Records ``logger.error`` / ``logger.warning`` calls for test assertions.

    Substitute for the module-level ``logger`` via ``monkeypatch.setattr``
    so tests can verify a structlog event was emitted with the expected
    structured kwargs. The shape matches the subset of
    ``structlog.BoundLogger`` the SEC-1 redaction helpers
    (``log_exception_redacted``, manual ``logger.error(EVENT, ...)``)
    actually exercise.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        """Recorded ``(event, kwargs)`` pairs in submission order."""

    def error(self, event: str, **kwargs: Any) -> None:
        """Record an ERROR-severity record under *event*."""
        self.calls.append((event, dict(kwargs)))

    def warning(self, event: str, **kwargs: Any) -> None:
        """Record a WARNING-severity record under *event*.

        Some redaction-helper call sites use ``logger.warning`` (e.g.
        rollback-step partial-failure paths). Captured alongside
        ``error`` because both severities flow through the same
        SEC-1 redacted-kwargs contract.
        """
        self.calls.append((event, dict(kwargs)))

    def info(self, event: str, **kwargs: Any) -> None:
        # The enforcer's success-path INFO logs flow through the same
        # logger object; discard so they do not interleave with the
        # ERROR / WARNING records the test cares about. AttributeError
        # would crash the production code path before the assertion;
        # accepting and discarding is the supported shape.
        del event, kwargs

    def debug(self, event: str, **kwargs: Any) -> None:
        # Same rationale as ``info``: production debug logs must not
        # crash the test when the capturing fake stands in for the
        # real BoundLogger.
        del event, kwargs
