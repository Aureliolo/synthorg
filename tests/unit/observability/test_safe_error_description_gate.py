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
import functools
import importlib.util
import textwrap
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GATE_SCRIPT = _REPO_ROOT / "scripts" / "check_logger_exception_str_exc.py"


@functools.lru_cache(maxsize=1)
def _load_gate_module() -> object:
    """Load the gate script as a module so we can call its internals.

    Importlib's spec/module form is the standard way to import a
    file that lives outside ``sys.path``; the ``scripts/`` directory
    is intentionally not on the package path. Result is cached because
    re-executing the gate's module body on every test call is the
    single largest cost in this test file (the gate registers AST
    visitors and pre-compiles regex patterns at import time).
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


def _scan_source(
    source: str,
    *,
    exc_info_allowlist_lines: frozenset[int] | None = None,
) -> list[tuple[int, int, str]]:
    """Run the gate's ``_LoggerExceptionFinder`` against an inline source.

    Each hit is a ``(lineno, col_offset, rule_id)`` triple where
    ``rule_id`` is one of ``error_str_exc`` / ``exc_info_true``;
    callers that only need the line / column can ignore the third
    element.
    """
    gate = _load_gate_module()
    finder_cls = gate._LoggerExceptionFinder  # type: ignore[attr-defined]
    if exc_info_allowlist_lines is None:
        finder = finder_cls()
    else:
        finder = finder_cls(exc_info_allowlist_lines=exc_info_allowlist_lines)
    finder.visit(ast.parse(textwrap.dedent(source)))
    hits: list[tuple[int, int, str]] = list(finder.hits)
    return hits


def _scan_source_e2e(source: str) -> list[tuple[int, int, str]]:
    """End-to-end: parse + tokenise allowlist + run finder.

    Uses the same code path as ``_scan_file`` but on inline source,
    so allowlist comments embedded in the source actually take
    effect. Use for tests that exercise the
    ``# lint-allow: exc-info -- <reason>`` parser.
    """
    gate = _load_gate_module()
    dedented = textwrap.dedent(source)
    allowlist = gate._collect_lint_allow_exc_info_lines(dedented)  # type: ignore[attr-defined]
    finder_cls = gate._LoggerExceptionFinder  # type: ignore[attr-defined]
    finder = finder_cls(exc_info_allowlist_lines=allowlist)
    finder.visit(ast.parse(dedented))
    return list(finder.hits)


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

    f-string interpolation does not produce a ``Call`` node; the
    matcher inspects ``FormattedValue`` directly so credentials
    routed through ``error=f"...{exc}..."`` are caught alongside
    the explicit ``str(<exc_like>)`` shape.
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

    def test_fstring_type_exc_name_quiet(self) -> None:
        """``error=f"{type(exc).__name__}"`` -- safe; type taxonomy only.

        ``type(exc).__name__`` is the canonical replacement for
        ``str(exc)`` -- it carries the exception class name without
        any of the args / message text. The walker must NOT descend
        into the ``type(exc)`` Call to find the inner ``exc``,
        because the Call's return value (a type object), not its
        argument, is what gets stringified.
        """
        hits = _scan_source(
            """
            logger.warning("E", error=f"{type(exc).__name__}")
            """,
        )
        assert not hits, "type(exc).__name__ interpolation must not be flagged"

    def test_fstring_safe_error_description_call_quiet(self) -> None:
        """``error=f"{safe_error_description(exc)}"`` -- redacted helper.

        The whole point of the helper is to be the safe replacement;
        wrapping its return value in an f-string is unusual but must
        not trip the gate.
        """
        hits = _scan_source(
            """
            logger.warning("E", error=f"{safe_error_description(exc)}")
            """,
        )
        assert not hits

    def test_fstring_type_with_static_prefix_quiet(self) -> None:
        """``error=f"prefix ({type(exc).__name__})"`` -- inner safe call.

        Reproduces the activities.py:79 false-positive that was
        flagged by an earlier walker that descended into ``Call.args``.
        The fix uses ``_walk_excluding_call_args`` so the Name ``exc``
        inside ``type(exc)`` does not match.
        """
        hits = _scan_source(
            """
            logger.warning(
                "E",
                error=f"failed; using fallback ({type(exc).__name__})",
            )
            """,
        )
        assert not hits, "static prefix + type(exc).__name__ is a safe shape, no leak"

    def test_fstring_exc_class_name_quiet(self) -> None:
        """``error=f"{exc.__class__.__name__}"`` -- type metadata, safe.

        Same shape as ``type(exc).__name__``: evaluates to the
        exception's class name, not its message.
        """
        hits = _scan_source(
            """
            logger.warning("E", error=f"{exc.__class__.__name__}")
            """,
        )
        assert not hits, "exc.__class__.__name__ is type metadata, no leak"

    def test_fstring_exc_class_qualname_quiet(self) -> None:
        """``error=f"{exc.__class__.__qualname__}"`` -- type metadata, safe."""
        hits = _scan_source(
            """
            logger.warning("E", error=f"{exc.__class__.__qualname__}")
            """,
        )
        assert not hits

    def test_fstring_method_call_on_exc_flagged(self) -> None:
        """``error=f"{exc.format_for_log()}"`` -- method on exc still flagged.

        The walker descends into ``Call.func`` (so attribute chains
        like ``exc.format_for_log`` still match) but skips
        ``Call.args``. A method call on the exception itself can
        return arbitrary leaked data; flag it.
        """
        hits = _scan_source(
            """
            logger.warning("E", error=f"{exc.format_for_log()}")
            """,
        )
        assert hits


@pytest.mark.unit
class TestVariableIndirection:
    """``error_msg = str(exc); logger.warning(error=error_msg)`` is the same leak.

    A naive AST gate that only inspects the kwarg value at the call
    site misses indirection through a one-step Name binding. The
    finder tracks per-function-scope leak aliases: any Name whose
    assignment RHS contains a known leak shape is flagged when later
    referenced as the ``error=`` kwarg.
    """

    def test_str_exc_alias_flagged(self) -> None:
        """``error_msg = str(exc); logger.warning(error=error_msg)``."""
        hits = _scan_source(
            """
            def f():
                try:
                    pass
                except Exception as exc:
                    error_msg = str(exc)
                    logger.warning("E", error=error_msg)
            """,
        )
        assert hits, "variable-indirection bypass must be flagged"

    def test_fstring_exc_alias_flagged(self) -> None:
        """``msg = f"{exc}"; logger.warning(error=msg)``."""
        hits = _scan_source(
            """
            def f():
                try:
                    pass
                except Exception as exc:
                    msg = f"{exc}"
                    logger.warning("E", error=msg)
            """,
        )
        assert hits, "f-string-aliased exc must be flagged via indirection"

    def test_safe_alias_quiet(self) -> None:
        """``msg = safe_error_description(exc); logger.warning(error=msg)`` -- safe."""
        hits = _scan_source(
            """
            def f():
                try:
                    pass
                except Exception as exc:
                    msg = safe_error_description(exc)
                    logger.warning("E", error=msg)
            """,
        )
        assert not hits, "redacted alias must not be flagged"

    def test_alias_with_or_fallback_flagged(self) -> None:
        """``error_msg = str(exc) or "fallback"; logger.warning(error=error_msg)``.

        The alias still binds to a leak shape (BoolOp value), so the
        gate's wrapper-walk catches it during alias collection.
        """
        hits = _scan_source(
            """
            def f():
                try:
                    pass
                except Exception as exc:
                    error_msg = str(exc) or "fallback"
                    logger.warning("E", error=error_msg)
            """,
        )
        assert hits

    def test_alias_in_nested_function_isolated(self) -> None:
        """Aliases collected in an inner function do not leak to outer scope."""
        hits = _scan_source(
            """
            def outer():
                def inner():
                    try:
                        pass
                    except Exception as exc:
                        error_msg = str(exc)
                        logger.warning("E", error=error_msg)
                logger.warning("E2", error=error_msg)
            """,
        )
        # Only the inner call is flagged. The outer call references
        # an out-of-scope name; the gate doesn't try to resolve that.
        assert len(hits) == 1, "inner alias must not bleed into outer scope"

    def test_alias_in_nested_lambda_isolated(self) -> None:
        """Aliases captured by a lambda do not leak alias status to the outer scope.

        Mirrors :meth:`test_alias_in_nested_function_isolated` for the
        ``Lambda`` boundary specifically -- the walker stops alias
        propagation at every nested scope (``FunctionDef`` /
        ``AsyncFunctionDef`` / ``Lambda``). Without this guard, an
        alias defined inside a lambda body would silently extend the
        outer scope's leak-alias set and start flagging unrelated
        outer logger calls.
        """
        hits = _scan_source(
            """
            def outer():
                try:
                    pass
                except Exception as exc:
                    fail = lambda: logger.warning(
                        "E",
                        error=(lambda msg=str(exc): msg)(),
                    )
                logger.warning("E2", error=error_msg)
            """,
        )
        # Only the lambda's own logger call references a leak shape;
        # the outer call references ``error_msg`` which was never
        # bound (lambdas do not bind into the enclosing scope), so it
        # must not be flagged via stale alias propagation.
        assert len(hits) == 1, "lambda-internal alias must not bleed into outer scope"

    def test_call_wrapped_alias_flagged(self) -> None:
        """``wrapped = passthrough(msg); logger.warning(error=wrapped)`` leaks.

        Without descending into ``Call.args`` during alias collection,
        a leak hidden behind an arbitrary identity-style call would
        slip past the gate. The alias-aware walker descends into call
        arguments and stops only at the documented safe boundaries
        (``safe_error_description(...)`` and class-introspection
        chains).
        """
        hits = _scan_source(
            """
            def f():
                try:
                    pass
                except Exception as exc:
                    msg = str(exc)
                    wrapped = passthrough(msg)
                    logger.warning("E", error=wrapped)
            """,
        )
        assert hits, "alias hidden in a passthrough call must trip the gate"

    def test_unrelated_alias_quiet(self) -> None:
        """``error_msg = "static"; logger.warning(error=error_msg)`` -- safe."""
        hits = _scan_source(
            """
            def f():
                error_msg = "static description"
                logger.warning("E", error=error_msg)
            """,
        )
        assert not hits, "non-leak alias must not be flagged"

    def test_alias_subscript_wrapped_flagged(self) -> None:
        """``error=error_msg[:200]`` references a leak alias under a slice."""
        hits = _scan_source(
            """
            def f():
                try:
                    pass
                except Exception as exc:
                    error_msg = str(exc)
                    logger.warning("E", error=error_msg[:200])
            """,
        )
        assert hits, (
            "subscript-wrapped alias reference must trip the gate; truncation "
            "still preserves the credential prefix"
        )

    def test_alias_boolop_wrapped_flagged(self) -> None:
        """``error=error_msg or "fallback"`` references a leak alias under BoolOp."""
        hits = _scan_source(
            """
            def f():
                try:
                    pass
                except Exception as exc:
                    error_msg = str(exc)
                    logger.warning("E", error=error_msg or "fallback")
            """,
        )
        assert hits, "BoolOp-wrapped alias reference must trip the gate"

    def test_transitive_alias_chain_flagged(self) -> None:
        """``msg = str(exc); safe = msg; logger.warning(error=safe)`` still leaks.

        A naive one-hop tracker only registers ``msg`` and misses the
        rebinding to ``safe``. The transitive arm of
        ``_collect_leak_aliases`` walks the RHS for any ``Name`` whose
        id is already in the alias set so the chain is closed at
        collection time.
        """
        hits = _scan_source(
            """
            def f():
                try:
                    pass
                except Exception as exc:
                    msg = str(exc)
                    safe = msg
                    logger.warning("E", error=safe)
            """,
        )
        assert hits, "transitive alias chain must trip the gate"

    def test_alias_module_scope_flagged(self) -> None:
        """Module-level ``error_msg = str(exc)`` aliases must be tracked.

        Without ``visit_Module`` registration, top-level assignments in
        an ``if __name__ == '__main__':`` block or a try/except at
        module scope would never reach the alias collector and the
        downstream logger call would slip through.
        """
        hits = _scan_source(
            """
            try:
                pass
            except Exception as exc:
                error_msg = str(exc)
                logger.warning("E", error=error_msg)
            """,
        )
        assert hits, "module-level alias must trip the gate"

    def test_alias_through_safe_error_description_not_flagged(self) -> None:
        """``error=safe_error_description(msg)`` must NOT trip the gate.

        The sanitizer is the documented remediation: passing a
        leak-alias through it cleans the value. The kwarg-side
        alias walker (``_has_error_alias_kwarg``) uses
        ``_walk_excluding_call_args`` so the inner ``Name`` inside
        the sanitizer's argument list does not propagate the alias
        match -- otherwise the documented fix path would itself trip
        the gate it was added to silence.
        """
        hits = _scan_source(
            """
            def f():
                try:
                    pass
                except Exception as exc:
                    msg = str(exc)
                    logger.warning("E", error=safe_error_description(msg))
            """,
        )
        assert not hits, "passing an alias through safe_error_description must not flag"

    def test_alias_rebound_through_safe_error_description_not_flagged(
        self,
    ) -> None:
        """``safe = safe_error_description(msg); error=safe`` must NOT trip.

        The transitive alias collector
        (``_value_subtree_references_leak_alias``) uses
        ``_walk_excluding_call_args`` so a rebinding through the
        sanitizer does not register ``safe`` as a leak alias. The
        downstream ``error=safe`` then passes cleanly. Without this
        carve-out an entire class of legitimate cleanups would be
        rejected by the gate they were added to satisfy.
        """
        hits = _scan_source(
            """
            def f():
                try:
                    pass
                except Exception as exc:
                    msg = str(exc)
                    safe = safe_error_description(msg)
                    logger.warning("E", error=safe)
            """,
        )
        assert not hits, (
            "rebinding through safe_error_description must not flag downstream"
        )


@pytest.mark.unit
class TestExcInfoGate:
    """``exc_info=True`` on logger calls leaks via traceback frame-locals.

    structlog's exc-info processor serialises the traceback's
    frame-local variables into the log event. Any ``client_secret``
    / ``refresh_token`` / Fernet ciphertext in the calling scope
    leaks to the log sink, regardless of how the ``error=`` field is
    constructed.

    The gate flags every ``exc_info=True`` literal kwarg on a logger
    call. Per-line opt-out via
    ``# lint-allow: exc-info -- <reason>`` (mandatory non-empty
    reason) covers genuine framework boundaries that already redact
    frame-locals downstream.
    """

    @pytest.mark.parametrize(
        "method",
        ["exception", "warning", "error", "info", "debug"],
    )
    def test_exc_info_true_flagged(self, method: str) -> None:
        """Every severity trips on ``exc_info=True`` literal."""
        hits = _scan_source(
            f"""
            logger.{method}("E", exc_info=True)
            """,
        )
        assert hits, f"logger.{method}(..., exc_info=True) must be flagged"

    def test_exc_info_false_quiet(self) -> None:
        """``exc_info=False`` is the explicit opt-out; do not flag."""
        hits = _scan_source(
            """
            logger.warning("E", exc_info=False)
            """,
        )
        assert not hits

    def test_exc_info_runtime_value_quiet(self) -> None:
        """Non-literal ``exc_info=<expr>`` is not the leak vector we target.

        The gate stays conservative: only the literal ``True`` kwarg
        is flagged. Runtime-decided exc_info is rare and opting it
        in/out at runtime is a legitimate (if uncommon) pattern.
        """
        hits = _scan_source(
            """
            logger.warning("E", exc_info=some_var)
            """,
        )
        assert not hits

    def test_attribute_logger_exc_info_flagged(self) -> None:
        """``self._logger.warning(..., exc_info=True)`` trips."""
        hits = _scan_source(
            """
            self._logger.warning("E", exc_info=True)
            """,
        )
        assert hits

    def test_non_logger_receiver_exc_info_quiet(self) -> None:
        """``siren.warning(..., exc_info=True)`` -- not a logger."""
        hits = _scan_source(
            """
            siren.warning("E", exc_info=True)
            """,
        )
        assert not hits

    def test_exc_info_dict_unpack_flagged(self) -> None:
        """``logger.warning(..., **{"exc_info": True})`` must trip the gate.

        Without the dict-unpack arm of ``_exc_info_kwarg_values`` a
        caller could re-enable traceback-frame-locals serialisation by
        smuggling the kwarg through ``**{"exc_info": True}`` and the
        gate would silently let the call ship.
        """
        hits = _scan_source(
            """
            logger.warning("E", **{"exc_info": True})
            """,
        )
        assert hits, "**{'exc_info': True} must be flagged like exc_info=True"
        assert any(rule == "exc_info_true" for _, _, rule in hits)

    def test_exc_info_dict_unpack_false_quiet(self) -> None:
        """``**{"exc_info": False}`` mirrors the literal-False opt-out."""
        hits = _scan_source(
            """
            logger.warning("E", **{"exc_info": False})
            """,
        )
        assert not hits

    def test_exc_info_with_fstring_error_double_flagged(self) -> None:
        """A call with both leaks records one hit per rule (str_exc + exc_info).

        The finder appends a hit for each rule that triggers, so a
        single call with both an f-string-interpolated exception AND a
        literal ``exc_info=True`` produces two hits with distinct rule
        IDs. Pinning the rule classification here protects against a
        regression where one of the two violations is silently absorbed
        into the other.
        """
        hits = _scan_source(
            """
            logger.warning("E", error=f"{exc}", exc_info=True)
            """,
        )
        rule_ids = {rule_id for _, _, rule_id in hits}
        assert rule_ids == {"error_str_exc", "exc_info_true"}, (
            f"expected both rule IDs to fire on a dual-leak call; got {rule_ids}"
        )

    def test_allowlist_marker_with_reason_not_flagged(self) -> None:
        """``# lint-allow: exc-info -- <reason>`` opts out a single line."""
        hits = _scan_source_e2e(
            """
            logger.warning(
                "E",
                exc_info=True,  # lint-allow: exc-info -- top-level handler
            )
            """,
        )
        assert not hits, (
            "allowlisted exc_info=True with non-empty reason must be permitted"
        )

    def test_allowlist_marker_empty_reason_still_flagged(self) -> None:
        """``# lint-allow: exc-info --`` (empty reason) must NOT opt out."""
        hits = _scan_source_e2e(
            """
            logger.warning(
                "E",
                exc_info=True,  # lint-allow: exc-info --
            )
            """,
        )
        assert hits, "allowlist requires a non-empty reason after `--`"

    def test_allowlist_marker_no_double_dash_still_flagged(self) -> None:
        """``# lint-allow: exc-info reason`` (no double-dash) must NOT opt out."""
        hits = _scan_source_e2e(
            """
            logger.warning(
                "E",
                exc_info=True,  # lint-allow: exc-info top-level handler
            )
            """,
        )
        assert hits, "allowlist requires the `--` separator before the reason"

    def test_allowlist_marker_only_covers_marked_line(self) -> None:
        """An allowlist comment opts out only the call on its line."""
        hits = _scan_source_e2e(
            """
            logger.warning(
                "E1",
                exc_info=True,  # lint-allow: exc-info -- top-level handler
            )
            logger.warning(
                "E2",
                exc_info=True,
            )
            """,
        )
        assert hits, "second unannotated call must still be flagged"
        assert len(hits) == 1, "only the unannotated call should remain in the hit list"

    def test_allowlist_marker_above_call_flagged(self) -> None:
        """An allowlist comment on a separate line above the call does NOT opt out.

        The marker must be on the same physical line as
        ``exc_info=True,`` so reviewers and tooling can locate the
        opt-out without scanning the file.
        """
        hits = _scan_source_e2e(
            """
            # lint-allow: exc-info -- handler
            logger.warning("E", exc_info=True)
            """,
        )
        assert hits, "marker not on the exc_info= line must not opt out"

    def test_collect_lint_allow_lines_returns_set(self) -> None:
        """The tokeniser returns the line numbers of valid allowlist comments."""
        gate = _load_gate_module()
        collect = gate._collect_lint_allow_exc_info_lines  # type: ignore[attr-defined]
        source = textwrap.dedent(
            """
            # lint-allow: exc-info -- valid reason
            x = 1  # lint-allow: exc-info -- another
            y = 2  # lint-allow: exc-info --
            z = 3  # lint-allow: exc-info something
            w = 4  # comment
            """,
        )
        lines = collect(source)
        assert 2 in lines, "valid solo-line marker must be collected"
        assert 3 in lines, "valid trailing marker must be collected"
        assert 4 not in lines, "empty-reason marker must NOT be collected"
        assert 5 not in lines, "missing `--` must NOT be collected"
        assert 6 not in lines, "plain comment must not be collected"


# Hypothesis strategies for the gate-fuzz tests below.
#
# The fuzz strategy synthesises Python source for
# ``logger.<method>(EVENT, error=<expr>)`` where ``<expr>`` is a
# wrapper tree of bounded depth around either a "leak" leaf
# (interpolating an exception) or a "safe" leaf
# (``safe_error_description(exc)`` / ``type(exc).__name__``).
#
# The property under test is set-membership: any tree that contains a
# leak leaf must trip the gate, any tree that contains only safe
# leaves must not. Don't model "is this materialisation safe" inside
# the fuzz -- that's the gate's job, not the test's.

_LEAK_LEAVES: tuple[str, ...] = (
    "str(exc)",
    'f"{exc}"',
    'f"{exc!s}"',
    'f"{exc!r}"',
    'f"{exc.args[0]}"',
    'f"{self._inner}"',
    "str(exc.args[0])",
    "str(self._inner)",
)
"""Expressions that the gate should always flag when used as ``error=``."""

_SAFE_LEAVES: tuple[str, ...] = (
    "safe_error_description(exc)",
    "type(exc).__name__",
    '"static literal"',
    'f"{strategy_name}"',
    'f"{config.field_name}"',
    'f"failed after {attempts}"',
)
"""Expressions that must NOT trip the gate even after wrapping."""


def _wrap(expr: str, kind: str) -> str:
    """Compose *expr* inside a single wrapper of *kind*.

    Wrappers preserve any leaks in *expr* (they don't redact); the
    gate's ``ast.walk`` traversal must catch leaks regardless of
    wrapper depth.
    """
    if kind == "subscript":
        return f"({expr})[:200]"
    if kind == "binop":
        return f"({expr}) + ' ctx'"
    if kind == "ifexp":
        return f"({expr}) if cond else 'fallback'"
    if kind == "boolop":
        return f"({expr}) or 'fallback'"
    if kind == "joinedstr":
        return f'f"{{{expr}}}"'
    msg = f"unknown wrapper kind: {kind}"
    raise ValueError(msg)


_WRAPPER_KINDS: tuple[str, ...] = ("subscript", "binop", "ifexp", "boolop")
"""Wrappers that preserve the inner expression as a subtree.

Excludes ``joinedstr`` because composing an arbitrary subtree
*inside* an f-string interpolation requires the subtree to be a
valid expression -- which our generated wrappers (containing string
literals) usually aren't. The four listed wrappers all accept
arbitrary sub-expressions.
"""


@st.composite
def _wrapped_expression(
    draw: st.DrawFn,
    leaves: tuple[str, ...],
    *,
    max_depth: int = 4,
) -> str:
    """Generate a Python expression wrapping a *leaves* element."""
    expr = draw(st.sampled_from(leaves))
    depth = draw(st.integers(min_value=0, max_value=max_depth))
    for _ in range(depth):
        kind = draw(st.sampled_from(_WRAPPER_KINDS))
        expr = _wrap(expr, kind)
    return expr


@pytest.mark.unit
class TestGateFuzz:
    """Property-based fuzz: wrapper combinatorics never hide a leak.

    Generates ``logger.<method>(EVENT, error=<expr>)`` calls with
    randomly stacked wrappers around either a leak leaf or a safe
    leaf. The gate must flag iff the leaf is a leak. Catches
    regressions in the wrapper-walk logic that targeted unit tests
    might miss when a new ``ast`` node type is introduced.

    ``derandomize=True`` is required for CI determinism: structlog
    failures on a flaky fuzz are expensive to diagnose, and the
    isolation gate (``run_affected_tests.py``) re-runs against the
    baseline twice, so non-deterministic test outcomes break the
    monotonic-shrink guarantee.
    """

    @given(_wrapped_expression(_LEAK_LEAVES))
    @settings(derandomize=True)
    def test_leak_leaf_always_flagged(self, expr: str) -> None:
        """Any wrapper around a leak leaf must trip the gate."""
        source = f"""
        logger.warning("E", error={expr})
        """
        hits = _scan_source(source)
        assert hits, f"wrapper around leak leaf must be flagged; got quiet on: {expr}"

    @given(_wrapped_expression(_SAFE_LEAVES))
    @settings(derandomize=True)
    def test_safe_leaf_never_flagged(self, expr: str) -> None:
        """Any wrapper around a safe leaf must NOT trip the gate."""
        source = f"""
        logger.warning("E", error={expr})
        """
        hits = _scan_source(source)
        assert not hits, (
            f"wrapper around safe leaf must not be flagged; got hit on: {expr}"
        )

    @given(
        st.booleans(),
        st.text(
            alphabet=st.characters(min_codepoint=33, max_codepoint=126),
            min_size=1,
            max_size=40,
        ),
    )
    @settings(derandomize=True)
    def test_exc_info_allowlist_respects_marker(
        self,
        allowlisted: bool,
        reason: str,
    ) -> None:
        """A non-empty reason after ``--`` opts out; bare allowlist does not.

        Generates a ``logger.warning(..., exc_info=True)`` call with
        either an ``exc-info -- <reason>`` marker (allowlisted) or no
        marker at all. The gate must flag iff there is no marker.
        """
        # The reason must contain at least one non-whitespace char to
        # match _ALLOW_EXC_INFO_RE; we strip leading whitespace and
        # discard empty draws since Hypothesis can produce all-space
        # strings inside the alphabet.
        cleaned_reason = reason.strip()
        if not cleaned_reason:
            return
        # Comments cannot contain a literal newline; reject reasons
        # whose printable codepoints survive but produce a multi-line
        # comment when rendered. The alphabet bounds (33-126) already
        # exclude newline (10), so this is a safety net.
        if "\n" in cleaned_reason:
            return
        if allowlisted:
            source = (
                'logger.warning("E", exc_info=True)  # lint-allow: '
                f"exc-info -- {cleaned_reason}\n"
            )
        else:
            source = 'logger.warning("E", exc_info=True)\n'
        hits = _scan_source_e2e(source)
        if allowlisted:
            assert not hits, (
                "valid allowlist marker must opt out; got hit with "
                f"reason={cleaned_reason!r}"
            )
        else:
            assert hits, "missing allowlist marker must be flagged"


@pytest.mark.integration
class TestRepoIsClean:
    """End-to-end check: the gate finds zero violations after the sweep.

    Marked ``integration`` rather than ``unit``: the test runs
    ``cmd_scan_all()`` which AST-walks every ``.py`` file under
    ``src/synthorg/`` (~600 files), well past the unit-suite per-test
    wall-clock budget. The pre-commit hook (``check_logger_exception_str_exc``)
    enforces the same gate locally, so unit-suite filtering does not lose
    coverage; this test is the CI/full-run safety net for the assembled
    repo state.
    """

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
