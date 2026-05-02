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
descendant ``str(<exc_like>)`` call regardless of how it is wrapped:

* ``logger.<method>(..., error=str(exc))``
* ``self._logger.<method>(..., error=str(exc))``
* ``audit_logger.<method>(..., error=str(exc))``
* ``error=str(exc.args[0])`` / ``error=str(self._inner)``
* ``error=str(exc)[:200]`` (subscript wrapper)
* ``error=str(exc)[:N] or type(exc).__name__`` (boolop wrapper)
* ``error=str(exc) if cond else fallback`` (ifexp wrapper)
* ``error=str(exc) + " context"`` (binop / concatenation wrapper)
* ``error=f"failed: {str(exc)}"`` (joinedstr / f-string wrapper)

Specifically, we flag a call when *all* of the following hold:

1. The callee is an ``Attribute`` whose terminal attribute is one of
   ``exception`` / ``warning`` / ``error`` / ``info`` / ``debug``
   (i.e. ``<anything>.<method>(...)``).
2. The receiver is either a bare ``Name`` whose identifier contains
   ``logger``, *or* an ``Attribute`` whose terminal attribute contains
   ``logger`` (the typical ``self._logger`` / ``self.audit_logger``
   shape).
3. One keyword argument has ``arg == "error"`` whose value subtree
   contains *anywhere* a ``Call`` to the builtin ``str`` with a single
   positional argument that is a ``Name``, ``Attribute``, or
   ``Subscript`` (covering ``str(exc)``, ``str(self._inner)``,
   ``str(exc.args[0])``). ``ast.walk`` descends through any wrapper
   construct (``Subscript`` / ``BoolOp`` / ``IfExp`` / ``BinOp`` /
   ``JoinedStr`` / ``Compare`` / future shapes) so no per-shape
   special-casing is needed; the gate stays current as the language
   adds new expression types.

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
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

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


class _LoggerExceptionFinder(ast.NodeVisitor):
    """Locate ``<logger>.<method>(..., error=str(exc_like))`` call sites.

    Attributes:
        hits: Tuples of ``(lineno, col_offset)`` for each match.
    """

    def __init__(self) -> None:
        self.hits: list[tuple[int, int]] = []

    def visit_Call(self, node: ast.Call) -> None:
        """Match ``<logger>.<method>(...)`` calls with ``error=str(exc_like)``."""
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in _LOGGER_METHODS
            and _is_logger_receiver(func.value)
            and _has_error_str_exc_kwarg(node.keywords)
        ):
            self.hits.append((node.lineno, node.col_offset))
        self.generic_visit(node)


def _has_error_str_exc_kwarg(keywords: Iterable[ast.keyword]) -> bool:
    """Return ``True`` if any keyword's value subtree contains ``str(<exc_like>)``.

    ``<exc_like>`` is ``ast.Name`` (``str(exc)``), ``ast.Attribute``
    (``str(self._inner)``), or ``ast.Subscript`` (``str(exc.args[0])``):
    all forms that could carry credential material through ``str``.

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
        values_to_scan: tuple[ast.expr, ...]
        if kw.arg == "error":
            values_to_scan = (kw.value,)
        elif kw.arg is None and isinstance(kw.value, ast.Dict):
            # ``**{"error": ...}`` or any literal-dict unpack: pull
            # every value whose key is the string constant ``"error"``.
            values_to_scan = tuple(
                value
                for key, value in zip(kw.value.keys, kw.value.values, strict=True)
                if isinstance(key, ast.Constant) and key.value == "error"
            )
        else:
            continue
        for value in values_to_scan:
            for node in ast.walk(value):
                if not isinstance(node, ast.Call):
                    continue
                if not isinstance(node.func, ast.Name) or node.func.id != "str":
                    continue
                if len(node.args) != 1:
                    continue
                arg = node.args[0]
                if isinstance(arg, (ast.Name, ast.Attribute, ast.Subscript)):
                    return True
    return False


class InspectionError(RuntimeError):
    """A source file could not be parsed or read for AST inspection.

    Raised from :func:`_scan_file` instead of silently returning "no
    hits" so a bad file fails the gate closed -- the alternative would
    let a deliberately-unparseable file sneak an unsafe site past CI.
    """


def _scan_file(path: Path) -> list[tuple[int, int]]:
    """Return the sorted list of ``(lineno, col_offset)`` hits in *path*.

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
    finder = _LoggerExceptionFinder()
    finder.visit(tree)
    return sorted(finder.hits)


def _iter_source_files() -> Iterable[Path]:
    """Walk ``src/synthorg/`` for ``.py`` files."""
    yield from sorted(_SRC_ROOT.rglob("*.py"))


def _rel(path: Path) -> str:
    """Repo-relative POSIX path for stable violation messages."""
    return path.resolve().relative_to(_REPO_ROOT).as_posix()


def _scan(src_path: Path) -> list[str]:
    """Return violation lines for *src_path*."""
    try:
        hits = _scan_file(src_path)
    except InspectionError as exc:
        return [f"{_rel(src_path)}: inspection failed: {exc}"]
    key = _rel(src_path)
    return [
        f"{key}:{lineno}:{col}: logger.<method>(..., error=str(exc)) site"
        for lineno, col in hits
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
        "\n`logger.<method>(..., error=str(exc))` leaks credential"
        " material via str(exc)-embedded URLs / form bodies (and via"
        " traceback frame-locals on ``logger.exception``)."
        "\nReplace with:"
        "\n    logger.warning("
        "\n        EVENT_NAME,"
        "\n        ...,"
        "\n        error_type=type(exc).__name__,"
        "\n        error=safe_error_description(exc),"
        "\n    )"
        "\n"
        "\nAdd: from synthorg.observability import safe_error_description",
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
