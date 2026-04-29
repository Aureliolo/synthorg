#!/usr/bin/env python3
"""Pre-commit gate: forbid ``logger.exception(..., error=str(exc))`` sites.

The pattern ``logger.exception(EVENT, ..., error=str(exc))`` is a known
secret-exfiltration vector on credential-handling code paths (SEC-1 /
audit finding 90):

* ``logger.exception`` attaches a full Python traceback; structlog
  serialises frame-local variables into the event, so any in-scope
  ``client_secret`` / ``refresh_token`` / Fernet ciphertext in the
  exception frame leaks to logs.
* ``str(exc)`` on ``httpx.HTTPStatusError`` frequently embeds the
  POST body or response body, which carries the credentials that
  triggered the failure.

This gate walks each file's AST and refuses any match.  The cleanup
that drained the originally-grandfathered population is complete
(#1638), so the rule is now unconditional: there is no allowlist, no
``--refresh-baseline`` escape hatch, and any match is a violation.

What we match
-------------

The matcher is deliberately broader than ``logger.exception`` to cover
every idiom seen in the tree:

* ``logger.exception(..., error=str(exc))``
* ``self._logger.exception(..., error=str(exc))``
* ``audit_logger.exception(..., error=str(exc))``
* ``error=str(exc.args[0])`` / ``error=str(self._inner)``

Specifically, we flag a call when *all* of the following hold:

1. The callee is an ``Attribute`` whose terminal attribute is
   ``exception`` (i.e. ``<anything>.exception(...)``).
2. The receiver is either a bare ``Name`` whose identifier contains
   ``logger``, *or* an ``Attribute`` whose terminal attribute contains
   ``logger`` (the typical ``self._logger`` / ``self.audit_logger``
   shape).
3. One keyword argument has ``arg == "error"`` and ``value`` is a
   ``Call`` to the builtin ``str`` with a single positional argument
   that is a ``Name``, ``Attribute``, or ``Subscript`` (covering
   ``str(exc)``, ``str(self._inner)``, ``str(exc.args[0])``).

To convert a flagged site, replace::

    logger.exception(EVENT, ..., error=str(exc))

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

_LOGGER_METHODS: frozenset[str] = frozenset({"exception"})
"""Which ``<receiver>.<method>(...)`` names are covered by this gate.

We only gate ``exception`` because it is the only log method that
attaches a Python traceback by default. ``logger.warning`` /
``logger.error`` do not attach traceback, so ``error=str(exc)`` in
those calls is a less severe concern handled at each callsite.
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
    """Return ``True`` if any keyword is ``error=str(<exc_like>)``.

    ``<exc_like>`` is ``ast.Name`` (``str(exc)``), ``ast.Attribute``
    (``str(self._inner)``), or ``ast.Subscript`` (``str(exc.args[0])``):
    all forms that could carry credential material through ``str``.
    """
    for kw in keywords:
        if kw.arg != "error":
            continue
        value = kw.value
        if not isinstance(value, ast.Call):
            continue
        if not isinstance(value.func, ast.Name) or value.func.id != "str":
            continue
        if len(value.args) != 1:
            continue
        arg = value.args[0]
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
        f"{key}:{lineno}:{col}: logger.exception(..., error=str(exc)) site"
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
        "\nSEC-1: `logger.exception(..., error=str(exc))` leaks credential"
        " material via traceback frame-locals AND str(exc) embedding."
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
