#!/usr/bin/env python3
"""Pre-commit gate: every ``CompletionConfig(...)`` must pin a temperature.

``providers.models.CompletionConfig.temperature`` defaults to ``None`` (the
provider's own default). The project runs a deliberate two-tier policy
(``docs/reference/conventions.md``):

* **System / utility prompts** pin ``temperature`` as a literal or named
  constant (``temperature=0.0`` / ``temperature=_PROPOSER_TEMPERATURE``).
* **Agent-execution prompts** inherit a runtime value from the agent's model
  config (``temperature=context.identity.model.temperature``).

Both tiers pass an explicit ``temperature=``; only *omitting* it (or passing
``temperature=None``) lets the contract drift to a silent provider default
that varies across backends -- the failure mode this gate closes.

The rule: any ``CompletionConfig(...)`` instantiation must carry a
``temperature=`` keyword whose value is not the ``None`` literal. A call that
spreads ``**kwargs`` is skipped (the temperature may arrive through the
unpacked mapping, which the AST cannot resolve).

Usage::

    python scripts/check_completion_config_temperature.py <file>...   # pre-commit
    python scripts/check_completion_config_temperature.py --scan-all  # CI / tests
"""

import argparse
import ast
import sys
from pathlib import Path
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from collections.abc import Iterable

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_ROOT = _REPO_ROOT / "src"


def _is_completion_config(func: ast.expr) -> bool:
    """Whether *func* names the ``CompletionConfig`` constructor."""
    if isinstance(func, ast.Name):
        return func.id == "CompletionConfig"
    if isinstance(func, ast.Attribute):
        return func.attr == "CompletionConfig"
    return False


def _has_kwargs_unpack(node: ast.Call) -> bool:
    """Whether the call spreads a mapping via ``**kwargs``.

    Such a call may supply ``temperature`` through the unpacked mapping, which
    the AST cannot resolve, so the gate skips it rather than false-flag.
    """
    return any(kw.arg is None for kw in node.keywords)


def _temperature_kwarg(node: ast.Call) -> ast.keyword | None:
    """Return the ``temperature=`` keyword of *node*, if present."""
    for kw in node.keywords:
        if kw.arg == "temperature":
            return kw
    return None


def _is_none_literal(value: ast.expr) -> bool:
    """Whether *value* is the ``None`` constant."""
    return isinstance(value, ast.Constant) and value.value is None


class InspectionError(RuntimeError):
    """A source file could not be read or parsed for inspection."""


class _CompletionConfigFinder(ast.NodeVisitor):
    """Collect ``CompletionConfig(...)`` calls that omit a real temperature."""

    def __init__(self) -> None:
        self.hits: list[int] = []

    @override
    def visit_Call(self, node: ast.Call) -> None:
        if _is_completion_config(node.func) and not _has_kwargs_unpack(node):
            kw = _temperature_kwarg(node)
            if kw is None or _is_none_literal(kw.value):
                self.hits.append(node.lineno)
        self.generic_visit(node)


def _scan_file(path: Path) -> list[int]:
    """Return the line numbers of offending calls in *path*.

    Raises:
        InspectionError: If the file cannot be read or parsed; the caller
            surfaces this as a violation so an unparseable file fails closed.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        msg = f"failed to read {path}: {type(exc).__name__}: {exc}"
        raise InspectionError(msg) from exc
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        msg = f"failed to parse {path}: SyntaxError at line {exc.lineno}: {exc.msg}"
        raise InspectionError(msg) from exc
    finder = _CompletionConfigFinder()
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
        f"{key}:{lineno}: CompletionConfig(...) without an explicit "
        "non-None temperature"
        for lineno in hits
    ]


def _report(violations: list[str]) -> int:
    """Print violations and return a pre-commit-friendly exit code."""
    if not violations:
        return 0
    for line in violations:
        print(line)
    print(
        "\nEvery CompletionConfig(...) must pin a temperature explicitly so the"
        " surface does not inherit a provider-default that varies across"
        " backends. Pin a literal / named constant for system prompts"
        " (temperature=0.0), or source it from the agent's model config"
        " (temperature=context.identity.model.temperature) for agent"
        " execution. Never omit it or pass temperature=None.",
        file=sys.stderr,
    )
    return 1


def cmd_scan_all() -> int:
    """Scan the whole src tree."""
    violations: list[str] = []
    for src_path in _iter_source_files():
        violations.extend(_scan(src_path))
    return _report(violations)


def cmd_scan_paths(paths: Iterable[str]) -> int:
    """Scan the given files (pre-commit entry point)."""
    violations: list[str] = []
    for raw in paths:
        path = Path(raw).resolve()
        if not path.is_relative_to(_SRC_ROOT):
            continue
        if not path.exists() or path.suffix != ".py":
            continue
        violations.extend(_scan(path))
    return _report(violations)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Gate CompletionConfig(...) calls on an explicit temperature.",
    )
    parser.add_argument("paths", nargs="*", help="Files to check (pre-commit).")
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
