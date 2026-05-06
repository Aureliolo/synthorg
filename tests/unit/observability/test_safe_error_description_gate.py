"""Tests for the extended secret-log gate.

``scripts/check_logger_exception_str_exc.py`` flags every logger
severity (``exception`` / ``warning`` / ``error`` / ``info`` /
``debug``) that passes ``error=str(exc)``. The script's filename is
preserved (historical pre-commit hook ID) but the method coverage and
matcher are broader than the original ``exception``-only gate.

These tests pin the gate's contract:

* Every severity method trips on the bare ``error=str(exc)`` form.
* Wrapped forms (``str(exc)[:200]``, ``str(exc) or fallback``,
  ``str(exc) if cond else fallback``) trip too: the matcher walks the
  kwarg value subtree and flags any descendant ``str(<exc_like>)``
  call. Truncating or fallback-fusing the credential-bearing string
  still leaks the prefix.
* Non-logger receivers and the canonical ``safe_error_description``
  replacement are not flagged.
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
    """The gate flags every logger severity, including wrapped str() forms."""

    @pytest.mark.parametrize(
        "method",
        ["exception", "warning", "error", "info", "debug"],
    )
    def test_logger_method_str_exc_flagged(self, method: str) -> None:
        """Every severity method trips the gate on bare ``error=str(exc)``."""
        hits = _scan_source(
            f"""
            logger.{method}("E", error=str(exc))
            """,
        )
        assert hits, f"logger.{method}(..., error=str(exc)) must be flagged"

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

    def test_str_exc_subscript_wrapper_flagged(self) -> None:
        """``error=str(exc)[:200]`` truncates but still leaks the prefix."""
        hits = _scan_source(
            """
            logger.error("E", error=str(exc)[:200])
            """,
        )
        assert hits, "subscript-wrapped str(exc) leaks the prefix; must be flagged"

    def test_str_exc_boolop_wrapper_flagged(self) -> None:
        """``error=str(exc) or fallback`` leaks str(exc) when truthy.

        Bare ``or`` only -- no ``[:200]`` slice -- so a regression in
        ``BoolOp`` traversal cannot be masked by a still-working
        ``Subscript`` traversal. ``test_str_exc_subscript_wrapper_flagged``
        already covers slicing; this test pins ``BoolOp`` independently.
        """
        hits = _scan_source(
            """
            logger.error("E", error=str(exc) or type(exc).__name__)
            """,
        )
        assert hits, "boolop-wrapped str(exc) is still a leak; must be flagged"

    def test_str_exc_ifexp_wrapper_flagged(self) -> None:
        """``error=str(exc) if cond else fallback`` leaks on the truthy arm."""
        hits = _scan_source(
            """
            logger.warning("E", error=str(exc) if condition else fallback)
            """,
        )
        assert hits, "ifexp-wrapped str(exc) is still a leak; must be flagged"

    def test_str_exc_binop_wrapper_flagged(self) -> None:
        """``error=str(exc) + " context"`` concatenates without scrubbing."""
        hits = _scan_source(
            """
            logger.warning("E", error=str(exc) + " context")
            """,
        )
        assert hits, "binop-wrapped str(exc) is still a leak; must be flagged"

    def test_str_exc_joinedstr_wrapper_flagged(self) -> None:
        """f-string interpolation of ``str(exc)`` carries the credential too."""
        hits = _scan_source(
            """
            logger.warning("E", error=f"failed: {str(exc)}")
            """,
        )
        assert hits, "f-string-wrapped str(exc) is still a leak; must be flagged"

    def test_str_exc_dict_unpack_flagged(self) -> None:
        """``**{"error": str(exc)}`` dict-unpack must trip the gate.

        Python represents this as ``ast.keyword(arg=None, value=Dict(...))``,
        so a naive ``kw.arg == "error"`` check sees no match -- the
        canonical bypass for an unconditional gate.
        """
        hits = _scan_source(
            """
            logger.warning("E", **{"error": str(exc)})
            """,
        )
        assert hits, "dict-unpack `error` value with str(exc) must be flagged"

    def test_str_exc_dict_unpack_wrapped_flagged(self) -> None:
        """Dict-unpack values are walked, so wrapped forms still trip.

        Bare ``or`` only, for the same isolation reason as
        ``test_str_exc_boolop_wrapper_flagged`` -- the dict-unpack path
        and the BoolOp-traversal path each get their own regression
        coverage.
        """
        hits = _scan_source(
            """
            logger.error("E", **{"error": str(exc) or "fallback"})
            """,
        )
        assert hits, "dict-unpack with wrapped str(exc) must be flagged"

    def test_dict_unpack_without_error_key_not_flagged(self) -> None:
        """Dict-unpack with no ``error`` key is left alone."""
        hits = _scan_source(
            """
            logger.warning("E", **{"context": str(exc)})
            """,
        )
        assert not hits, (
            "dict-unpack on a non-error key is out of scope -- the gate is "
            "specifically about the `error=` field"
        )


@pytest.mark.unit
class TestFStringBlindspot:
    """f-string interpolation of an exception is the same leak as ``str(exc)``.

    A ``FormattedValue`` with default conversion (``-1``) calls
    ``__format__`` on the value, which for ``BaseException`` falls back
    to ``str(exc)`` -- embedding any credential-bearing message text
    that ``str(exc)`` would. ``!s`` makes this explicit; ``!r``
    (``repr(exc)``) and ``!a`` (``ascii(exc)``) embed ``exc.args``,
    which on ``HTTPStatusError`` / ``psycopg.Error`` carries the same
    payload.

    The pre-2026-05 gate matched only explicit ``str(<exc_like>)``
    Call nodes; f-string interpolation slipped past because no Call
    is involved. These tests pin the new ``FormattedValue`` matcher.
    """

    @pytest.mark.parametrize(
        "method",
        ["exception", "warning", "error", "info", "debug"],
    )
    def test_fstring_implicit_exc_flagged(self, method: str) -> None:
        """Every severity trips on ``error=f"...{exc}..."`` (default conversion)."""
        hits = _scan_source(
            f"""
            logger.{method}("E", error=f"{{type(exc).__name__}}: {{exc}}")
            """,
        )
        assert hits, (
            f'logger.{method}(..., error=f"...{{exc}}...") leaks str(exc) '
            "via implicit FormattedValue conversion; must be flagged"
        )

    def test_fstring_exc_with_static_prefix_flagged(self) -> None:
        """Non-leading FormattedValue still tripped (walker descends JoinedStr)."""
        hits = _scan_source(
            """
            logger.warning("E", error=f"prefix {exc} suffix")
            """,
        )
        assert hits, "f-string with mid-position {exc} must be flagged"

    def test_fstring_explicit_str_conversion_flagged(self) -> None:
        """``error=f"{exc!s}"`` -- explicit ``__format__`` via ``str()``."""
        hits = _scan_source(
            """
            logger.warning("E", error=f"{exc!s}")
            """,
        )
        assert hits, "explicit !s conversion must be flagged"

    def test_fstring_explicit_repr_conversion_flagged(self) -> None:
        """``error=f"{exc!r}"`` -- ``repr(exc)`` embeds ``exc.args``."""
        hits = _scan_source(
            """
            logger.warning("E", error=f"{exc!r}")
            """,
        )
        assert hits, "explicit !r conversion must be flagged"

    def test_fstring_explicit_ascii_conversion_flagged(self) -> None:
        """``error=f"{exc!a}"`` -- ``ascii(exc)`` still embeds ``exc.args``."""
        hits = _scan_source(
            """
            logger.warning("E", error=f"{exc!a}")
            """,
        )
        assert hits, "explicit !a conversion must be flagged"

    def test_fstring_attribute_leaf_in_allowlist_flagged(self) -> None:
        """``error=f"{self._inner}"`` -- attr ``_inner`` is in the allowlist."""
        hits = _scan_source(
            """
            logger.warning("E", error=f"{self._inner}")
            """,
        )
        assert hits, "Attribute leaf with allowlisted attr must be flagged"

    def test_fstring_subscript_over_allowlist_attr_flagged(self) -> None:
        """``error=f"{exc.args[0]}"`` -- walker finds Name ``exc`` inside."""
        hits = _scan_source(
            """
            logger.warning("E", error=f"{exc.args[0]}")
            """,
        )
        assert hits, "Subscript over allowlist Name must be flagged"

    def test_fstring_cause_name_flagged(self) -> None:
        """``error=f"{cause}"`` -- ``cause`` is in ``_EXCEPTION_LEAF_NAMES``."""
        hits = _scan_source(
            """
            logger.warning("E", error=f"{cause}")
            """,
        )
        assert hits, "allowlist Name 'cause' must be flagged"

    def test_fstring_static_text_only_quiet(self) -> None:
        """Plain f-string with no interpolation is fine (no FormattedValue)."""
        hits = _scan_source(
            """
            logger.warning("E", error=f"static text")
            """,
        )
        assert not hits, "static-only f-string carries no leak"

    def test_fstring_non_allowlist_name_quiet(self) -> None:
        """``error=f"Unknown strategy: {strategy_name}"`` -- not exception-like."""
        hits = _scan_source(
            """
            logger.warning("E", error=f"Unknown strategy: {strategy_name}")
            """,
        )
        assert not hits, (
            "non-exception identifier in FormattedValue must not trip; "
            "plain string variables can't carry credential payloads"
        )

    def test_fstring_attempts_count_quiet(self) -> None:
        """``error=f"failed after {attempts}"`` -- ``attempts`` not in allowlist."""
        hits = _scan_source(
            """
            logger.warning("E", error=f"failed after {attempts}")
            """,
        )
        assert not hits

    def test_fstring_non_allowlist_attr_quiet(self) -> None:
        """``error=f"{config.field_name}"`` -- terminal attr not exception-like."""
        hits = _scan_source(
            """
            logger.warning("E", error=f"{config.field_name}")
            """,
        )
        assert not hits, (
            "Attribute terminal attr not in allowlist must not trip; "
            "config / settings access is not a leak vector"
        )

    def test_fstring_subscript_wrapper_flagged(self) -> None:
        """``error=f"{exc}"[:200]`` -- truncation preserves the prefix leak."""
        hits = _scan_source(
            """
            logger.warning("E", error=f"{exc}"[:200])
            """,
        )
        assert hits, "subscript-wrapped f-string must be flagged"

    def test_fstring_binop_wrapper_flagged(self) -> None:
        """``error=f"{exc}" + " ctx"`` -- concatenation does not scrub."""
        hits = _scan_source(
            """
            logger.warning("E", error=f"{exc}" + " ctx")
            """,
        )
        assert hits, "binop-wrapped f-string must be flagged"

    def test_fstring_ifexp_wrapper_flagged(self) -> None:
        """``error=f"{exc}" if cond else fallback`` -- truthy arm leaks."""
        hits = _scan_source(
            """
            logger.warning("E", error=f"{exc}" if cond else "fallback")
            """,
        )
        assert hits, "ifexp-wrapped f-string must be flagged"

    def test_fstring_boolop_wrapper_flagged(self) -> None:
        """``error=f"{exc}" or fallback`` -- truthy left side leaks."""
        hits = _scan_source(
            """
            logger.warning("E", error=f"{exc}" or "fallback")
            """,
        )
        assert hits, "boolop-wrapped f-string must be flagged"

    def test_fstring_dict_unpack_flagged(self) -> None:
        """``**{"error": f"{exc}"}`` -- dict-unpack value walked."""
        hits = _scan_source(
            """
            logger.warning("E", **{"error": f"{exc}"})
            """,
        )
        assert hits, "dict-unpack with f-string-interpolated exc must be flagged"


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
