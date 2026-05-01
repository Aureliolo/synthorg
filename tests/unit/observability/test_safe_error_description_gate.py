"""Tests for the extended secret-log gate.

``scripts/check_logger_exception_str_exc.py`` now flags
``logger.exception``, ``logger.warning``, and ``logger.error`` calls
that pass ``error=str(exc)``. The script's filename is preserved
(historical pre-commit hook ID) but the method coverage has been
broadened.

These tests pin the gate's contract: the three credential-bearing
method names trip; ``info`` / ``debug`` are unaffected; non-logger
sites with the same shape are not flagged.
"""

import ast
import importlib.util
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GATE_SCRIPT = _REPO_ROOT / "scripts" / "check_logger_exception_str_exc.py"


def _load_gate_module() -> object:
    """Load the gate script as a module so we can call its internals.

    Importlib's spec/module form is the standard way to import a
    file that lives outside ``sys.path``; the ``scripts/`` directory
    is intentionally not on the package path.
    """
    spec = importlib.util.spec_from_file_location(
        "_check_logger_gate",
        _GATE_SCRIPT,
    )
    if spec is None or spec.loader is None:
        msg = f"unable to load gate script at {_GATE_SCRIPT}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scan_source(source: str) -> list[tuple[int, int]]:
    """Run the gate's ``_LoggerExceptionFinder`` against an inline source."""
    gate = _load_gate_module()
    finder_cls = gate._LoggerExceptionFinder  # type: ignore[attr-defined]
    finder = finder_cls()
    finder.visit(ast.parse(textwrap.dedent(source)))
    hits: list[tuple[int, int]] = list(finder.hits)
    return hits


@pytest.mark.unit
class TestExtendedGate:
    """The gate flags exception/warning/error and skips info/debug."""

    def test_logger_exception_str_exc_flagged(self) -> None:
        hits = _scan_source(
            """
            logger.exception("E", error=str(exc))
            """,
        )
        assert hits, "logger.exception(..., error=str(exc)) must be flagged"

    def test_logger_warning_str_exc_flagged(self) -> None:
        hits = _scan_source(
            """
            logger.warning("E", error=str(exc))
            """,
        )
        assert hits, "logger.warning(..., error=str(exc)) must be flagged"

    def test_logger_error_str_exc_flagged(self) -> None:
        hits = _scan_source(
            """
            logger.error("E", error=str(exc))
            """,
        )
        assert hits, "logger.error(..., error=str(exc)) must be flagged"

    def test_logger_info_str_exc_not_flagged(self) -> None:
        hits = _scan_source(
            """
            logger.info("E", error=str(exc))
            """,
        )
        assert not hits, "logger.info is out of scope -- gate must skip"

    def test_logger_debug_str_exc_not_flagged(self) -> None:
        hits = _scan_source(
            """
            logger.debug("E", error=str(exc))
            """,
        )
        assert not hits, "logger.debug is out of scope -- gate must skip"

    def test_attribute_logger_warning_flagged(self) -> None:
        """Attribute-chain receivers (``self._logger.warning(...)``) trip."""
        hits = _scan_source(
            """
            self._logger.warning("E", error=str(exc))
            """,
        )
        assert hits

    def test_audit_logger_error_flagged(self) -> None:
        """Custom logger names containing ``logger`` are covered."""
        hits = _scan_source(
            """
            audit_logger.error("E", error=str(exc))
            """,
        )
        assert hits

    def test_safe_error_description_value_not_flagged(self) -> None:
        """The recommended replacement form is allowed."""
        hits = _scan_source(
            """
            logger.warning(
                "E",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            """,
        )
        assert not hits, "the safe_error_description replacement must be permitted"

    def test_non_logger_receiver_not_flagged(self) -> None:
        """A method named ``warning`` on a non-logger receiver is fine."""
        hits = _scan_source(
            """
            siren.warning("E", error=str(exc))
            """,
        )
        assert not hits

    def test_str_subscript_argument_flagged(self) -> None:
        """``error=str(exc.args[0])`` is also a leak vector and is flagged."""
        hits = _scan_source(
            """
            logger.error("E", error=str(exc.args[0]))
            """,
        )
        assert hits


@pytest.mark.unit
class TestRepoIsClean:
    """End-to-end check: the gate finds zero violations after the sweep."""

    def test_scan_all_returns_zero_violations(self) -> None:
        """``--scan-all`` exits 0 across the post-sweep ``src/synthorg/``."""
        gate = _load_gate_module()
        cmd_scan_all = gate.cmd_scan_all  # type: ignore[attr-defined]
        rc = cmd_scan_all()
        assert rc == 0, (
            "extended secret-log gate found violations -- run "
            "`uv run python scripts/check_logger_exception_str_exc.py "
            "--scan-all` for the list and convert each to "
            "`safe_error_description(exc)`"
        )
