#!/usr/bin/env python3
"""Pre-commit gate: forbid ``span.record_exception(...)`` in production code.

OpenTelemetry's :meth:`Span.record_exception` adds an ``exception``
event whose attributes carry the full traceback (and, on the
default OTel SDK exception handler, frame-locals) into the OTLP
exporter -- bypassing every :func:`safe_error_description` /
:func:`scrub_secret_tokens` redaction the structlog sink applies.
The cross-transport redaction policy lives at
``docs/reference/sec-prompt-safety.md``; this gate enforces it.

What we match
-------------

Any ``Call`` whose ``func`` is an ``Attribute`` with terminal
attribute ``record_exception`` (i.e. ``<receiver>.record_exception(...)``).
The receiver is unconstrained -- ``span.record_exception(exc)``,
``self._span.record_exception(exc)``, ``current_span().record_exception(exc)``
all match. We also flag two SDK kwargs on
``tracer.start_as_current_span`` / ``Tracer.start_span`` that re-enable
the auto-exception handler:

* ``record_exception=True`` -- forces the SDK to call
  ``span.record_exception`` in its own __exit__.
* ``set_status_on_exception=True`` -- forces the SDK to derive the
  span status from ``str(exc)``, which carries POST bodies / URLs on
  ``HTTPStatusError`` and friends.

Both kwargs default to ``True`` upstream, so the safe shape is
explicit ``record_exception=False, set_status_on_exception=False``
on every span that wraps a code path that might raise.

Scope
-----

* ``src/synthorg/`` only: tests / scripts / docs are out of scope.
* Per-line opt-out: ``# lint-allow: otlp-span-redaction -- <reason>``
  on the same physical line as the violating call. The reason is
  mandatory and must contain at least one non-whitespace character
  after ``--``. Modelled on the existing ``exc-info`` /
  ``persistence-boundary`` opt-out shape.

Usage
-----

::

    python scripts/check_otlp_span_redaction.py <file>...   # pre-commit
    python scripts/check_otlp_span_redaction.py --scan-all  # CI
"""

import argparse
import ast
import io
import re
import sys
import tokenize
from pathlib import Path
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from collections.abc import Iterable

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_ROOT = _REPO_ROOT / "src"

_FORBIDDEN_METHOD = "record_exception"
_FORBIDDEN_KWARGS_TRUE = frozenset({"record_exception", "set_status_on_exception"})
"""Span/tracer kwargs whose ``True`` value re-enables the SDK's
auto-exception handler. Both must be explicitly ``False`` in
production span bodies that wrap raisable code paths."""

_ALLOW_RE: re.Pattern[str] = re.compile(
    r"#\s*lint-allow:\s*otlp-span-redaction\s*--\s*\S",
)


def _collect_allow_lines(source: str) -> frozenset[int]:
    """Return physical line numbers carrying a valid opt-out marker."""
    allow: set[int] = set()
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok in tokens:
            if tok.type != tokenize.COMMENT:
                continue
            if _ALLOW_RE.search(tok.string):
                allow.add(tok.start[0])
    except tokenize.TokenError, IndentationError, SyntaxError:
        return frozenset()
    return frozenset(allow)


class _Finder(ast.NodeVisitor):
    """AST walker collecting ``record_exception`` / unsafe-kwargs sites."""

    def __init__(self, allow: frozenset[int]) -> None:
        self.allow = allow
        self.violations: list[tuple[int, int, str]] = []

    @override
    def visit_Call(self, node: ast.Call) -> None:
        # ``<receiver>.record_exception(...)``
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == _FORBIDDEN_METHOD
            and node.lineno not in self.allow
        ):
            self.violations.append(
                (
                    node.lineno,
                    node.col_offset,
                    f"{_FORBIDDEN_METHOD}() forbidden in production code; "
                    "use scrubbed exception.type/exception.message attributes "
                    "(see docs/reference/sec-prompt-safety.md)",
                )
            )
        # ``record_exception=True`` / ``set_status_on_exception=True``
        for kw in node.keywords:
            if (
                kw.arg in _FORBIDDEN_KWARGS_TRUE
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                and kw.lineno not in self.allow
            ):
                self.violations.append(
                    (
                        kw.lineno,
                        kw.col_offset,
                        f"{kw.arg}=True re-enables the OTel SDK's "
                        "auto-exception handler; set to False and write "
                        "scrubbed attributes manually",
                    )
                )
        self.generic_visit(node)


def _scan_file(path: Path) -> list[tuple[Path, int, int, str]]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        print(f"{path}: syntax error: {exc}", file=sys.stderr)
        return [(path, exc.lineno or 0, exc.offset or 0, "syntax error")]
    allow = _collect_allow_lines(source)
    finder = _Finder(allow)
    finder.visit(tree)
    return [(path, lno, col, msg) for (lno, col, msg) in finder.violations]


def _iter_targets(args: list[str]) -> Iterable[Path]:
    if args == ["--scan-all"]:
        for path in _SRC_ROOT.rglob("*.py"):
            yield path
        return
    for arg in args:
        path = Path(arg).resolve()
        if not path.is_file() or path.suffix != ".py":
            continue
        try:
            path.relative_to(_SRC_ROOT)
        except ValueError:
            continue
        yield path


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: scan ``argv`` (or ``--scan-all``) and exit non-zero on hits."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="Files to scan")
    parser.add_argument("--scan-all", action="store_true", help="Scan src/ recursively")
    ns = parser.parse_args(argv)
    raw = ["--scan-all"] if ns.scan_all else ns.files
    found: list[tuple[Path, int, int, str]] = []
    for path in _iter_targets(raw):
        found.extend(_scan_file(path))
    if not found:
        return 0
    print(
        "OTLP span redaction violations -- forbidden patterns in src/synthorg/:",
        file=sys.stderr,
    )
    for path, lno, col, msg in found:
        rel = path.relative_to(_REPO_ROOT)
        print(f"  {rel}:{lno}:{col}: {msg}", file=sys.stderr)
    print(
        "\nFix: replace ``span.record_exception(exc)`` with explicit "
        "``span.set_attribute('exception.type', type(exc).__name__)`` and "
        "``span.set_attribute('exception.message', safe_error_description(exc))``; "
        "and pair it with ``record_exception=False, set_status_on_exception=False`` "
        "on the surrounding ``start_as_current_span(...)``.",
        file=sys.stderr,
    )
    print(
        "Per-line opt-out: ``# lint-allow: otlp-span-redaction -- <reason>``.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
