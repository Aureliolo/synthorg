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


def _scan_source(
    source: str,
    *,
    exc_info_allowlist_lines: frozenset[int] | None = None,
) -> list[tuple[int, int]]:
    """Run the gate's ``_LoggerExceptionFinder`` against an inline source."""
    gate = _load_gate_module()
    finder_cls = gate._LoggerExceptionFinder  # type: ignore[attr-defined]
    if exc_info_allowlist_lines is None:
        finder = finder_cls()
    else:
        finder = finder_cls(exc_info_allowlist_lines=exc_info_allowlist_lines)
    finder.visit(ast.parse(textwrap.dedent(source)))
    hits: list[tuple[int, int]] = list(finder.hits)
    return hits


def _scan_source_e2e(source: str) -> list[tuple[int, int]]:
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

    def test_exc_info_with_fstring_error_double_flagged(self) -> None:
        """A call with both leaks (f-string + exc_info=True) is one finder hit.

        Hits are recorded per-call (``visit_Call`` appends once per
        match), so a single call with two distinct rule violations
        appears once. The combined ``--scan-all`` run reports the
        site once but the rule classification is internal.
        """
        hits = _scan_source(
            """
            logger.warning("E", error=f"{exc}", exc_info=True)
            """,
        )
        assert hits

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
