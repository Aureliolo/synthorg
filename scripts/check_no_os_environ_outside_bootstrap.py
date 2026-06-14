#!/usr/bin/env python3
"""Pre-push gate: single-key ``os.environ`` reads only at the bootstrap edge.

Configuration precedence is DB > env > code default via ``SettingsService`` /
``ConfigResolver`` (Cat-1) or env > code default via the bootstrap resolver
(Cat-2). Reading a named environment variable directly anywhere else is drift:
it bypasses the registry that owns the env-var name + default, and it scatters
config resolution across business logic instead of confining it to the
construction edge.

This gate flags single-key environment reads::

    os.environ.get("SYNTHORG_FOO")
    os.environ["SYNTHORG_FOO"]
    os.environ.pop("SYNTHORG_FOO")
    os.getenv("SYNTHORG_FOO")

It deliberately does NOT flag whole-environment snapshots used to build a
child-process environment (``os.environ.copy()``, ``dict(os.environ)``,
``os.environ.items()``, ``{**os.environ, ...}``) nor ``os.environ`` passed as
a default argument (the dependency-injection seam ``env: Mapping = os.environ``)
-- those are categorically different from a config read.

Reads are permitted only in the explicit bootstrap / entry-point / dynamic
secret-backend / Cat-3 construction-time allowlist below, or on a line (or its
contiguous comment block) carrying::

    # lint-allow: env-read -- <reason>

The justification after ``--`` is required and must be non-empty.

Fail-closed: any unparseable file under ``src/synthorg/`` is a gate violation.
"""

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, override

if TYPE_CHECKING:
    from collections.abc import Iterable

_SRC_ROOT: Final = Path(__file__).resolve().parent.parent / "src" / "synthorg"

_LINT_ALLOW_RE: Final = re.compile(
    r"#\s*lint-allow:\s*env-read\s*--\s*\S+",
)

# Modules permitted to read a single environment variable directly. Each is a
# bootstrap resolver, a process entry point, a dynamic-key secret backend, a
# config-template loader, or a Cat-3 construction-time class that validates its
# key at __init__ (the sanctioned fail-fast pattern). Paths are POSIX, relative
# to ``src/synthorg/``.
_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        # Bootstrap resolvers + settings precedence tiers.
        "settings/bootstrap_resolver.py",
        "settings/service.py",
        "settings/encryption.py",
        "config/loader.py",
        # API construction + boot.
        "api/app_builders.py",
        "api/app_helpers.py",
        "api/boot_persistence.py",
        "api/cursor_config.py",
        "api/auth/secret.py",
        "observability/startup_wiring.py",
        # Process entry points.
        "workers/__main__.py",
        "memory/embedding/fine_tune_runner.py",
        # Cat-3 construction-time secret/cipher classes.
        "integrations/tunnel/ngrok_adapter.py",
        "integrations/oauth/pkce.py",
        # Dynamic-key secret backends (the env var name is the secret id).
        "persistence/secret_backends/env_var.py",
        "persistence/secret_backends/factory.py",
        "persistence/secret_backends/encrypted_postgres.py",
        "persistence/secret_backends/encrypted_sqlite.py",
        # Tool sidecar entry points that read their own process args/env.
        "tools/browser/_executor.py",
        "tools/desktop/_executor.py",
        "tools/sandbox/_subprocess_proc.py",
    }
)


@dataclass(frozen=True)
class _Violation:
    """A single-key env read outside the bootstrap allowlist."""

    path: Path
    line: int
    snippet: str


def _is_os_environ(node: ast.expr) -> bool:
    """Return True if *node* is the ``os.environ`` attribute chain."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _is_os_getenv(func: ast.expr) -> bool:
    """Return True if *func* is the ``os.getenv`` attribute chain."""
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "getenv"
        and isinstance(func.value, ast.Name)
        and func.value.id == "os"
    )


class _EnvReadVisitor(ast.NodeVisitor):
    """Collect single-key ``os.environ`` / ``os.getenv`` read nodes."""

    def __init__(self) -> None:
        self.hits: list[tuple[int, str]] = []

    @override
    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if _is_os_getenv(func):
            self.hits.append((node.lineno, "os.getenv(...)"))
        elif (
            isinstance(func, ast.Attribute)
            and func.attr in ("get", "pop")
            and _is_os_environ(func.value)
        ):
            self.hits.append((node.lineno, f"os.environ.{func.attr}(...)"))
        self.generic_visit(node)

    @override
    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _is_os_environ(node.value):
            self.hits.append((node.lineno, "os.environ[...]"))
        self.generic_visit(node)


def _has_lint_allow(lines: list[str], lineno: int) -> bool:
    """Return True if *lineno* (or its contiguous comment block) opts out.

    Scans the flagged line and the unbroken run of ``#`` comment lines
    directly above it for the ``env-read`` marker.
    """
    idx = lineno - 1
    if 0 <= idx < len(lines) and _LINT_ALLOW_RE.search(lines[idx]):
        return True
    cursor = idx - 1
    while cursor >= 0 and lines[cursor].lstrip().startswith("#"):
        if _LINT_ALLOW_RE.search(lines[cursor]):
            return True
        cursor -= 1
    return False


def _check_file(path: Path) -> list[_Violation]:
    """Return env-read violations in *path* (parse failure is a violation)."""
    rel = path.relative_to(_SRC_ROOT).as_posix()
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return [_Violation(path, exc.lineno or 0, f"unparseable: {exc.msg}")]
    if rel in _ALLOWLIST:
        return []
    visitor = _EnvReadVisitor()
    visitor.visit(tree)
    if not visitor.hits:
        return []
    lines = text.splitlines()
    return [
        _Violation(path, lineno, snippet)
        for lineno, snippet in visitor.hits
        if not _has_lint_allow(lines, lineno)
    ]


def _iter_target_files(paths: Iterable[Path]) -> list[Path]:
    """Expand *paths* to the ``.py`` files under ``src/synthorg/`` to check."""
    out: list[Path] = []
    for raw in paths:
        path = raw.resolve()
        if path.is_dir():
            out.extend(sorted(path.rglob("*.py")))
        elif path.suffix == ".py":
            out.append(path)
    return [p for p in out if _SRC_ROOT in p.parents or p == _SRC_ROOT]


def main(argv: list[str] | None = None) -> int:
    """Run the gate. Returns 0 when clean, 1 on any violation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to check (default: all of src/synthorg).",
    )
    args = parser.parse_args(argv)
    targets = _iter_target_files(args.paths) if args.paths else [_SRC_ROOT]
    files = _iter_target_files(targets)

    violations: list[_Violation] = []
    for path in files:
        violations.extend(_check_file(path))

    if not violations:
        return 0

    print("Direct os.environ reads outside the bootstrap allowlist:\n")
    for v in sorted(violations, key=lambda x: (str(x.path), x.line)):
        rel = v.path.relative_to(_SRC_ROOT).as_posix()
        print(f"  src/synthorg/{rel}:{v.line}: {v.snippet}")
    print(
        "\nResolve config via SettingsService / ConfigResolver (Cat-1) or "
        "settings.bootstrap_resolver.resolve_init_value (Cat-2). If this is a "
        "genuine bootstrap / entry-point / Cat-3 construction read, add the "
        "module to the allowlist in this gate or annotate the line with "
        "'# lint-allow: env-read -- <reason>'."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
