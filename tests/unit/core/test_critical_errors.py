"""Tests for the interpreter-critical exception propagation helper."""

import pytest

from synthorg.core.critical_errors import _reraise_critical

pytestmark = pytest.mark.unit


class TestReraiseCritical:
    """Truth table for `_reraise_critical`.

    The helper distinguishes interpreter-critical exceptions (re-raise) from
    every other `Exception` subclass (return None) so the caller's broad
    `except Exception:` handler can continue with logging / recovery only
    for non-critical errors.
    """

    def test_memory_error_re_raises(self) -> None:
        with pytest.raises(MemoryError, match="out of memory"):
            _reraise_critical(MemoryError("out of memory"))

    def test_recursion_error_re_raises(self) -> None:
        with pytest.raises(RecursionError, match="stack overflow"):
            _reraise_critical(RecursionError("stack overflow"))

    @pytest.mark.parametrize(
        "exc",
        [
            ValueError("bad value"),
            KeyError("missing"),
            TypeError("wrong type"),
            RuntimeError("generic runtime"),
            Exception("base exception"),
            OSError("disk failure"),
            ArithmeticError("division by zero"),
        ],
        ids=["value", "key", "type", "runtime", "exception", "os", "arithmetic"],
    )
    def test_ordinary_exceptions_pass_through(self, exc: Exception) -> None:
        _reraise_critical(exc)

    @pytest.mark.parametrize(
        "exc",
        [
            KeyboardInterrupt(),
            SystemExit(0),
            GeneratorExit(),
        ],
        ids=["keyboard-interrupt", "system-exit", "generator-exit"],
    )
    def test_base_exception_non_critical_passes_through(
        self,
        exc: BaseException,
    ) -> None:
        _reraise_critical(exc)

    def test_recursion_error_is_runtime_error_subclass(self) -> None:
        """Guards against a future Python that changes the hierarchy."""
        assert issubclass(RecursionError, RuntimeError)
        with pytest.raises(RecursionError):
            _reraise_critical(RecursionError())

    def test_memory_error_is_exception_subclass(self) -> None:
        """Guards against a future Python that changes the hierarchy.

        The whole point of the helper is to re-raise `MemoryError` even
        though a broad `except Exception:` would otherwise catch it.
        """
        assert issubclass(MemoryError, Exception)


_MEMORY_MSG = "simulated OOM"
_VALUE_MSG = "ordinary failure"


def _broad_handler_with_critical_passthrough(
    to_raise: BaseException,
    sentinel: list[str],
) -> None:
    """Reproduce the standard call-site pattern.

    Wraps the production pattern: a `try` that raises some exception,
    followed by a broad `except Exception:` that calls
    `_reraise_critical` before its recovery / logging logic. The
    sentinel tracks whether the recovery path ran.
    """
    try:
        raise to_raise  # noqa: TRY301 -- intentional re-raise for test setup
    except Exception as exc:
        _reraise_critical(exc)
        sentinel.append("logged")


class TestUsagePattern:
    """The helper's typical call site: inside a broad `except Exception:`."""

    def test_memory_error_propagates_past_broad_handler(self) -> None:
        sentinel: list[str] = []
        with pytest.raises(MemoryError):
            _broad_handler_with_critical_passthrough(
                MemoryError(_MEMORY_MSG),
                sentinel,
            )
        assert sentinel == [], (
            "broad-handler recovery must not run when _reraise_critical re-raises"
        )

    def test_ordinary_exception_lets_broad_handler_run(self) -> None:
        sentinel: list[str] = []
        _broad_handler_with_critical_passthrough(
            ValueError(_VALUE_MSG),
            sentinel,
        )
        assert sentinel == ["logged"]
