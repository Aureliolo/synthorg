#!/usr/bin/env python3
"""Pre-commit gate: forbid ``logger.<method>(..., error=str(exc))`` sites.

The pattern ``logger.<method>(EVENT, ..., error=str(exc))`` -- on any
severity (``exception``, ``warning``, ``error``, ``info``, ``debug``)
-- is a known secret-exfiltration vector on credential-handling code
paths:

* ``logger.exception`` attaches a full Python traceback; structlog
  serialises frame-local variables into the event, so any in-scope
  ``client_secret`` / ``refresh_token`` / Fernet ciphertext in the
  exception frame leaks to logs.
* ``str(exc)`` on ``httpx.HTTPStatusError`` / ``psycopg.Error`` /
  most third-party HTTP clients embeds the POSTed body, query
  string, or response body in the exception message -- which carries
  the credentials that triggered the failure. The embedded-URL/body
  risk is independent of severity: a ``debug`` / ``info`` / ``warning``
  / ``error`` call still ends up shipping the credential to whatever
  log sink the operator is using.

This gate walks each file's AST and refuses any match. The rule is
unconditional: there is no allowlist, no ``--refresh-baseline``
escape hatch, and any match is a violation. The script's filename
is preserved (rather than renamed) so the pre-commit hook ID
``no-new-logger-exception-str-exc`` (registered in
``.pre-commit-config.yaml``) stays stable across CI job references.

What we match
-------------

The matcher covers every idiom seen in the tree, including wrapped
forms that truncate or fall back to a type name (``str(exc)[:200]``,
``str(exc) or fallback``). Truncation does not eliminate the leak:
even 200 bytes of an OAuth error can carry a ``client_secret`` query
parameter.

The example wrappers below are non-exhaustive; the matcher works by
descending the kwarg value subtree via ``ast.walk`` and flagging any
descendant ``str(<exc_like>)`` call OR any ``FormattedValue`` (f-string
interpolation) of an exception-like reference, regardless of how it
is wrapped:

* ``logger.<method>(..., error=str(exc))``
* ``self._logger.<method>(..., error=str(exc))``
* ``audit_logger.<method>(..., error=str(exc))``
* ``error=str(exc.args[0])`` / ``error=str(self._inner)``
* ``error=str(exc)[:200]`` (subscript wrapper)
* ``error=str(exc)[:N] or type(exc).__name__`` (boolop wrapper)
* ``error=str(exc) if cond else fallback`` (ifexp wrapper)
* ``error=str(exc) + " context"`` (binop / concatenation wrapper)
* ``error=f"failed: {str(exc)}"`` (joinedstr with explicit ``str()``)
* ``error=f"{type(exc).__name__}: {exc}"`` (joinedstr, implicit
  ``__format__`` -> ``str(exc)`` via FormattedValue conversion ``-1``)
* ``error=f"{exc!s}"`` / ``error=f"{exc!r}"`` / ``error=f"{exc!a}"``
  (explicit conversion: ``str`` / ``repr`` / ``ascii`` -- all embed
  exception args)

Specifically, we flag a call when *all* of the following hold:

1. The callee is an ``Attribute`` whose terminal attribute is one of
   ``exception`` / ``warning`` / ``error`` / ``info`` / ``debug``
   (i.e. ``<anything>.<method>(...)``).
2. The receiver is either a bare ``Name`` whose identifier contains
   ``logger``, *or* an ``Attribute`` whose terminal attribute contains
   ``logger`` (the typical ``self._logger`` / ``self.audit_logger``
   shape).
3. One keyword argument has ``arg == "error"`` whose value subtree
   contains *anywhere* one of:
   a. A ``Call`` to the builtin ``str`` with a single positional
      argument that is a ``Name``, ``Attribute``, or ``Subscript``
      (covering ``str(exc)``, ``str(self._inner)``,
      ``str(exc.args[0])``).
   b. A ``FormattedValue`` whose ``conversion`` is in
      ``_FSTRING_LEAK_CONVERSIONS`` (default / ``!s`` / ``!r`` /
      ``!a``) AND whose interpolated leaf is exception-like (Name id
      or Attribute attr in ``_EXCEPTION_LEAF_NAMES``). The leaf
      allowlist (``exc`` / ``e`` / ``err`` / ``error`` / ``exception``
      / ``cause`` / ``original`` / ``inner`` / ``_inner``) is narrower
      than the ``str(<arg>)`` shape gate to avoid false positives on
      ``error=f"prefix {strategy_name}"``-style innocuous
      interpolations.

   ``ast.walk`` descends through any wrapper construct (``Subscript``
   / ``BoolOp`` / ``IfExp`` / ``BinOp`` / ``JoinedStr`` / ``Compare``
   / future shapes) so no per-shape special-casing is needed; the
   gate stays current as the language adds new expression types.

To convert a flagged site, replace::

    logger.<method>(EVENT, ..., error=str(exc))

with::

    logger.warning(
        EVENT,
        ...,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )

(``from synthorg.observability import safe_error_description``)

Usage::

    python scripts/check_logger_exception_str_exc.py <file>...     # pre-commit
    python scripts/check_logger_exception_str_exc.py --scan-all    # CI / tests
"""

import argparse
import ast
import io
import re
import sys
import tokenize
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_ROOT = _REPO_ROOT / "src"

_LOGGER_METHODS: frozenset[str] = frozenset(
    {"exception", "warning", "error", "info", "debug"},
)
"""Which ``<receiver>.<method>(...)`` names are covered by this gate.

The traceback attachment risk is unique to ``exception``, but the
``str(exc)``-embedding risk on ``HTTPStatusError`` / ``psycopg.Error``
/ most third-party HTTP clients applies equally to every severity: a
credential that ends up in the exception's message string leaks to
whatever sink the logger is wired to, regardless of whether the
traceback is also attached. Coverage is therefore unconditional
across all five severity methods.
"""


_EXCEPTION_LEAF_NAMES: frozenset[str] = frozenset(
    {
        "exc",
        "e",
        "err",
        "error",
        "exception",
        "cause",
        "original",
        "inner",
        "_inner",
    },
)
"""Identifier names treated as exception-bearing references.

Used by the ``FormattedValue`` matcher to discriminate
``error=f"...{exc}..."`` (a credential leak: ``exc.__format__`` falls
back to ``str(exc)``, which carries POST bodies / URLs on
``HTTPStatusError`` and friends) from
``error=f"Unknown strategy: {strategy_name}"`` (a plain string
variable -- not a leak vector).

The set is intentionally narrower than the existing
``str(<arg>)``-shape matcher (which flags any
Name/Attribute/Subscript leaf). The asymmetry reflects empirical use:
``str(strategy_name)`` is essentially never written by hand because
``strategy_name`` is already a string; ``f"{strategy_name}"`` is
common because f-strings exist for concatenation. Restricting the
f-string matcher to known exception names keeps false positives at
zero while still catching every exception-bearing interpolation.

Adding a new exception variable name (``ex``, ``problem``, ...) to
the project requires extending this set; CLAUDE.md §Logging already
prescribes structured kwargs for non-exception data, so divergent
patterns should be rare.
"""

_FSTRING_LEAK_CONVERSIONS: frozenset[int] = frozenset({-1, 115, 114, 97})
"""``ast.FormattedValue.conversion`` values that materialise the leaf.

* ``-1``: default ``__format__``; for ``BaseException`` this is
  ``str(exc)``.
* ``115`` (``!s``, ``ord('s')``): explicit ``str(exc)``.
* ``114`` (``!r``, ``ord('r')``): ``repr(exc)`` -- embeds ``exc.args``.
* ``97`` (``!a``, ``ord('a')``): ``ascii(exc)`` -- still embeds
  ``exc.args``, just escapes non-ASCII bytes.

All four shapes can carry credential material; flag every one of
them.
"""

_ALLOW_EXC_INFO_RE: re.Pattern[str] = re.compile(
    r"#\s*lint-allow:\s*exc-info\s*--\s*\S",
)
"""Per-line opt-out marker for ``exc_info=True``.

Mirrors the existing CLAUDE.md gate marker shape (``persistence-boundary``,
``dual-backend-parity``, ``currency-aggregation``, ``bootstrap-wiring``):
``# lint-allow: <gate-name> -- <reason>`` with a mandatory non-empty
reason. The trailing ``\\S`` anchor enforces "at least one
non-whitespace character after the ``--``" so empty / placeholder
reasons do not pass.
"""


def _collect_lint_allow_exc_info_lines(source: str) -> frozenset[int]:
    """Return the set of physical line numbers carrying a valid opt-out.

    Tokenises *source* with ``tokenize.generate_tokens`` and filters
    ``COMMENT`` tokens whose text matches ``_ALLOW_EXC_INFO_RE``. The
    returned set is intersected against ``ast.keyword.value.lineno``
    in the finder so a marker on the same physical line as
    ``exc_info=True,`` opts out only that call.

    A bad source (one that ``tokenize`` cannot read) returns an empty
    set; the AST gate already fails closed via :class:`InspectionError`
    on parse failure, so this is the correct choice for the comment
    layer too.
    """
    allow_lines: set[int] = set()
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok in tokens:
            if tok.type != tokenize.COMMENT:
                continue
            if _ALLOW_EXC_INFO_RE.search(tok.string):
                allow_lines.add(tok.start[0])
    except tokenize.TokenError, IndentationError, SyntaxError:
        # Fail-closed at the parse layer, not here. Return an empty
        # allowlist; if the source has tokenisation problems the
        # AST scan will surface InspectionError separately.
        return frozenset()
    return frozenset(allow_lines)


_TYPE_INTROSPECTION_ATTRS: frozenset[str] = frozenset(
    {"__class__", "__name__", "__qualname__", "__module__"},
)
"""Attribute names that resolve to type metadata, not exception data.

``exc.__class__.__name__`` and ``exc.__class__.__qualname__`` produce
the exception's type name -- the same shape as
``type(exc).__name__``, which is a documented safe shape. These
chains are fully constituted by static class introspection and
carry no credential material, so the walker stops descending at the
terminal-introspection attribute.
"""


def _walk_excluding_call_args(expr: ast.expr) -> Iterator[ast.AST]:
    """Yield nodes in *expr*'s subtree, skipping ``Call.args`` / ``Call.keywords``.

    The interpolated value of an f-string is whatever the expression
    *evaluates to*. For a ``Call`` node, the args are passed to the
    function and the return value is what gets stringified -- the args
    themselves do not materialise in the f-string. So
    ``f"{type(exc).__name__}"`` interpolates the type's name (safe);
    ``f"{safe_error_description(exc)}"`` interpolates the helper's
    return value (safe by intent). Both contain a Name ``exc`` deep in
    the AST, but a naive ``ast.walk`` would flag them as leaks.

    Class-introspection chains (``exc.__class__.__name__``,
    ``exc.__class__.__qualname__``) are also safe -- they evaluate to
    type metadata, not exception data. The walker stops at any
    Attribute access whose ``attr`` is in ``_TYPE_INTROSPECTION_ATTRS``.

    This walker visits the Call expression and its ``func`` (so a
    method call on an exception, e.g. ``f"{exc.format_for_log()}"``,
    still trips on the ``func`` chain), but does NOT recurse into the
    Call's positional / keyword arguments.
    """
    yield expr
    if isinstance(expr, ast.Call):
        yield from _walk_excluding_call_args(expr.func)
        return
    if isinstance(expr, ast.Attribute) and expr.attr in _TYPE_INTROSPECTION_ATTRS:
        # Class-introspection chain terminates here; the underlying
        # Name (e.g. ``exc``) is bound to a type / class / qualname
        # value that does not carry credential material.
        return
    for child in ast.iter_child_nodes(expr):
        if isinstance(child, ast.expr):
            yield from _walk_excluding_call_args(child)


def _formatted_value_references_exception(node: ast.FormattedValue) -> bool:
    """Return ``True`` if *node* interpolates an exception-like reference.

    Walks the FormattedValue's ``value`` subtree (skipping ``Call``
    arguments -- see :func:`_walk_excluding_call_args`) and returns
    ``True`` on the first ``Name`` whose ``id`` is in
    ``_EXCEPTION_LEAF_NAMES`` or ``Attribute`` whose terminal ``attr``
    is in the same set. Handles wrappers like ``exc.args[0]``
    (Subscript over Attribute on Name ``exc``) and excludes the
    canonical safe shapes ``type(exc).__name__`` and
    ``safe_error_description(exc)``.
    """
    for inner in _walk_excluding_call_args(node.value):
        if isinstance(inner, ast.Name) and inner.id in _EXCEPTION_LEAF_NAMES:
            return True
        if isinstance(inner, ast.Attribute) and inner.attr in _EXCEPTION_LEAF_NAMES:
            return True
    return False


def _is_logger_receiver(value: ast.expr) -> bool:
    """Return ``True`` if *value* looks like a logger binding.

    Matches bare names (``logger``, ``audit_logger``) as well as
    attribute chains whose terminal attribute contains ``logger``
    (``self._logger``, ``self.audit_logger``, ``cls.logger``, ...).
    """
    if isinstance(value, ast.Name):
        return "logger" in value.id
    if isinstance(value, ast.Attribute):
        return "logger" in value.attr
    return False


_RULE_STR_EXC = "error_str_exc"
_RULE_EXC_INFO = "exc_info_true"


class _LoggerExceptionFinder(ast.NodeVisitor):
    """Locate logger-call leak sites covered by either rule.

    Two rules are tracked:

    * ``error_str_exc``: ``error=`` value subtree contains
      ``str(<exc_like>)`` or an exception-interpolating
      ``FormattedValue``.
    * ``exc_info_true``: ``exc_info=True`` literal kwarg with no
      ``# lint-allow: exc-info -- <reason>`` marker on the same
      physical line as the ``exc_info=`` keyword value.

    Attributes:
        hits: Triples of ``(lineno, col_offset, rule)`` where *rule*
            is one of the ``_RULE_*`` constants. A single call may
            contribute up to two hits (one per rule) if both fire.
        exc_info_allowlist_lines: Frozen set of physical line numbers
            whose ``exc_info=True,`` keyword has been opted out via
            an inline allowlist comment.
    """

    def __init__(
        self,
        *,
        exc_info_allowlist_lines: frozenset[int] = frozenset(),
    ) -> None:
        self.hits: list[tuple[int, int, str]] = []
        self.exc_info_allowlist_lines = exc_info_allowlist_lines
        # Names known to alias a leak shape in the current function
        # scope, e.g. ``error_msg = str(exc)`` rebinds ``error_msg``
        # to credential-bearing material. Cleared when entering a new
        # function. Outer-scope (module-level) aliases live on the
        # base set for the module body.
        self._leak_aliases: set[str] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Collect leak-aliases for the function then recurse."""
        self._visit_function_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Collect leak-aliases for the async function then recurse."""
        self._visit_function_scope(node)

    def _visit_function_scope(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        """Walk *node* with a fresh leak-alias set for this scope."""
        previous_aliases = self._leak_aliases
        # Inherit outer aliases (closures see them) but additions made
        # in this scope must not leak back out.
        self._leak_aliases = set(previous_aliases)
        for stmt in node.body:
            self._collect_leak_aliases(stmt)
        for child in ast.iter_child_nodes(node):
            self.visit(child)
        self._leak_aliases = previous_aliases

    def _collect_leak_aliases(self, node: ast.AST) -> None:
        """Record any ``Name = <leak-shape>`` assignment in *node*'s subtree.

        Descends through ``try`` / ``with`` / ``if`` blocks within the
        function body but stops at nested ``FunctionDef`` /
        ``AsyncFunctionDef`` / ``Lambda`` boundaries -- those define
        their own scope and get their own alias set when visited.
        """
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return  # nested scope -- handled by its own visit_FunctionDef pass
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            value = node.value
            if value is not None and _value_subtree_leaks(value):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                for target in targets:
                    if isinstance(target, ast.Name):
                        self._leak_aliases.add(target.id)
        for child in ast.iter_child_nodes(node):
            self._collect_leak_aliases(child)

    def visit_Call(self, node: ast.Call) -> None:
        """Match ``<logger>.<method>(...)`` calls against both rules."""
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in _LOGGER_METHODS
            and _is_logger_receiver(func.value)
        ):
            if _has_error_str_exc_kwarg(node.keywords) or _has_error_alias_kwarg(
                node.keywords,
                self._leak_aliases,
            ):
                self.hits.append(
                    (node.lineno, node.col_offset, _RULE_STR_EXC),
                )
            exc_info_lineno = _find_unallowlisted_exc_info_true(
                node.keywords,
                self.exc_info_allowlist_lines,
            )
            if exc_info_lineno is not None:
                self.hits.append(
                    (exc_info_lineno, node.col_offset, _RULE_EXC_INFO),
                )
        self.generic_visit(node)


def _has_error_alias_kwarg(
    keywords: Iterable[ast.keyword],
    leak_aliases: set[str],
) -> bool:
    """Return ``True`` if ``error=<Name>`` references a known leak alias.

    Catches the variable-indirection bypass:

        error_msg = str(exc)              # alias added to leak set
        logger.warning(EVENT, error=error_msg)   # caught here

    Only one level of aliasing is tracked. Multi-hop aliasing
    (``a = str(exc); b = a; logger.warning(error=b)``) requires a
    transitive analysis pass that the project currently does not need;
    the gate stays conservative.
    """
    if not leak_aliases:
        return False
    for kw in keywords:
        for value in _error_kwarg_values(kw):
            if isinstance(value, ast.Name) and value.id in leak_aliases:
                return True
    return False


def _find_unallowlisted_exc_info_true(
    keywords: Iterable[ast.keyword],
    allowlist_lines: frozenset[int],
) -> int | None:
    """Return the lineno of an unallowlisted ``exc_info=True`` kwarg, if any.

    Returns ``None`` when no ``exc_info=True`` literal kwarg is
    present, or when every such kwarg sits on a line in
    ``allowlist_lines``. The lineno returned is the
    ``ast.keyword.value.lineno`` so callers can render the violation
    pointing at the offending keyword, not at the call's opening
    paren.
    """
    for kw in keywords:
        if kw.arg != "exc_info":
            continue
        if not isinstance(kw.value, ast.Constant) or kw.value.value is not True:
            continue
        if kw.value.lineno in allowlist_lines:
            continue
        return kw.value.lineno
    return None


def _is_str_exc_call(node: ast.AST) -> bool:
    """Return ``True`` if *node* is ``str(<exc_like>)``.

    ``<exc_like>`` is ``ast.Name`` (``str(exc)``), ``ast.Attribute``
    (``str(self._inner)``), or ``ast.Subscript`` (``str(exc.args[0])``):
    all forms that could carry credential material through ``str``.
    """
    if not isinstance(node, ast.Call):
        return False
    if not isinstance(node.func, ast.Name) or node.func.id != "str":
        return False
    if len(node.args) != 1:
        return False
    return isinstance(node.args[0], (ast.Name, ast.Attribute, ast.Subscript))


def _is_fstring_exc_leak(node: ast.AST) -> bool:
    """Return ``True`` if *node* is an exception-bearing FormattedValue.

    Default conversion (``-1``) materialises ``str(exc)`` via
    ``__format__``; explicit ``!s`` / ``!r`` / ``!a`` carry the same
    credential payload. The interpolated leaf is matched against the
    narrow ``_EXCEPTION_LEAF_NAMES`` allowlist.
    """
    if not isinstance(node, ast.FormattedValue):
        return False
    if node.conversion not in _FSTRING_LEAK_CONVERSIONS:
        return False
    return _formatted_value_references_exception(node)


def _value_subtree_leaks(value: ast.expr) -> bool:
    """Return ``True`` if any descendant of *value* is a known leak shape."""
    return any(
        _is_str_exc_call(node) or _is_fstring_exc_leak(node) for node in ast.walk(value)
    )


def _has_error_str_exc_kwarg(keywords: Iterable[ast.keyword]) -> bool:
    """Return ``True`` if any keyword's value subtree contains a leak shape.

    The walk descends through any wrapper expression (``Subscript`` for
    truncation, ``BoolOp`` / ``IfExp`` for fallback fusion, ``BinOp`` /
    ``JoinedStr`` for concatenation), so a leak hidden behind ``[:200]``
    or ``or type(exc).__name__`` still trips the gate.

    Dict-unpack arguments (``**{"error": str(exc)}``) are also covered:
    Python represents these as ``ast.keyword(arg=None, value=Dict(...))``
    rather than ``arg="error"``, which would otherwise sneak past a
    naive ``kw.arg == "error"`` check -- the very escape hatch this
    gate exists to close.
    """
    for kw in keywords:
        for value in _error_kwarg_values(kw):
            if _value_subtree_leaks(value):
                return True
    return False


def _error_kwarg_values(kw: ast.keyword) -> tuple[ast.expr, ...]:
    """Return the value subtrees that map to an ``error=`` field.

    Covers both the direct ``error=...`` keyword and dict-unpack
    forms (``**{"error": ...}``) where ``kw.arg`` is ``None`` and
    the value is a literal ``ast.Dict``.
    """
    if kw.arg == "error":
        return (kw.value,)
    if kw.arg is None and isinstance(kw.value, ast.Dict):
        return tuple(
            value
            for key, value in zip(kw.value.keys, kw.value.values, strict=True)
            if isinstance(key, ast.Constant) and key.value == "error"
        )
    return ()


class InspectionError(RuntimeError):
    """A source file could not be parsed or read for AST inspection.

    Raised from :func:`_scan_file` instead of silently returning "no
    hits" so a bad file fails the gate closed -- the alternative would
    let a deliberately-unparseable file sneak an unsafe site past CI.
    """


def _scan_file(path: Path) -> list[tuple[int, int, str]]:
    """Return the sorted ``(lineno, col_offset, rule)`` hits in *path*.

    Raises:
        InspectionError: If the file cannot be read or parsed. The
            caller MUST surface this as a gate violation; skipping
            unparseable files would let an attacker ship a payload
            that scanners cannot inspect.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        # PEP 758's no-parens form is only valid *without* an ``as``
        # binding; mixing ``as exc`` with the comma list is a grammar
        # error under Python 3.14, so the parens are required here.
        msg = f"failed to read {path}: {type(exc).__name__}: {exc}"
        raise InspectionError(msg) from exc
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        msg = f"failed to parse {path}: SyntaxError at line {exc.lineno}: {exc.msg}"
        raise InspectionError(msg) from exc
    allowlist_lines = _collect_lint_allow_exc_info_lines(source)
    finder = _LoggerExceptionFinder(exc_info_allowlist_lines=allowlist_lines)
    finder.visit(tree)
    return sorted(finder.hits)


def _iter_source_files() -> Iterable[Path]:
    """Walk ``src/synthorg/`` for ``.py`` files."""
    yield from sorted(_SRC_ROOT.rglob("*.py"))


def _rel(path: Path) -> str:
    """Repo-relative POSIX path for stable violation messages."""
    return path.resolve().relative_to(_REPO_ROOT).as_posix()


_RULE_MESSAGES: dict[str, str] = {
    _RULE_STR_EXC: "logger.<method>(..., error=str(exc)) site",
    _RULE_EXC_INFO: "logger.<method>(..., exc_info=True) site",
}


def _scan(src_path: Path) -> list[str]:
    """Return violation lines for *src_path*."""
    try:
        hits = _scan_file(src_path)
    except InspectionError as exc:
        return [f"{_rel(src_path)}: inspection failed: {exc}"]
    key = _rel(src_path)
    return [
        f"{key}:{lineno}:{col}: {_RULE_MESSAGES[rule]}" for lineno, col, rule in hits
    ]


def cmd_scan_all() -> int:
    """Scan the whole src tree."""
    violations: list[str] = []
    for src_path in _iter_source_files():
        violations.extend(_scan(src_path))
    return _report(violations)


def cmd_scan_paths(paths: Iterable[str]) -> int:
    """Scan the given files (pre-commit entry point)."""
    violations: list[str] = []
    for p in paths:
        path = Path(p).resolve()
        if not path.is_relative_to(_SRC_ROOT):
            continue
        if not path.exists() or path.suffix != ".py":
            continue
        violations.extend(_scan(path))
    return _report(violations)


def _report(violations: list[str]) -> int:
    """Print violations and return a pre-commit-friendly exit code."""
    if not violations:
        return 0
    for line in violations:
        print(line)
    print(
        "\n`logger.<method>(..., error=str(exc))` and"
        ' `error=f"...{exc}..."` leak credential material via'
        " str(exc)-embedded URLs / form bodies."
        " `logger.<method>(..., exc_info=True)` leaks via traceback"
        " frame-locals (any in-scope client_secret / refresh_token)."
        "\n"
        "\nReplace error=str(exc) / f-string-exc with:"
        "\n    logger.warning("
        "\n        EVENT_NAME,"
        "\n        ...,"
        "\n        error_type=type(exc).__name__,"
        "\n        error=safe_error_description(exc),"
        "\n    )"
        "\n"
        "\nAdd: from synthorg.observability import safe_error_description"
        "\n"
        "\nFor exc_info=True: drop it (the redacted error= field"
        " carries the type taxonomy operators need for triage), or"
        " for genuine framework boundaries that already redact"
        " frame-locals downstream, opt out per-line with:"
        "\n    exc_info=True,  # lint-allow: exc-info -- <reason>",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Gate on logger.exception(..., error=str(exc)) sites.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files to check (pre-commit supplies these).",
    )
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="Scan the full src tree (CI mode).",
    )
    args = parser.parse_args(argv)

    if args.scan_all:
        return cmd_scan_all()
    return cmd_scan_paths(args.paths)


if __name__ == "__main__":
    sys.exit(main())
