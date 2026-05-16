#!/usr/bin/env python3
"""Pre-push / CI gate: forbid ``synthorg.api.dto_*`` imports in persistence and service layers.

The persistence and service layers must not depend on API-layer DTOs.
DTOs that need to be shared with the persistence layer live in the
domain module they describe (e.g.
``synthorg.providers.management.capability_dtos``); the ``api.dto_*``
modules are HTTP-facing aliases and importing them from the
persistence layer creates a layering cycle.

The gate scans ``*.py`` files under:

* ``src/synthorg/persistence/``
* ``src/synthorg/api/services/`` (the service layer)

and flags any ``from synthorg.api.dto_<name> import ...`` or
``import synthorg.api.dto_<name>`` statement. ``synthorg.api`` imports
that are not ``dto_*`` (controllers, services, lifecycle helpers,
etc.) are out of scope; the gate only targets DTO modules.

Two invocation modes:

* No positional args: scan the persistence/service trees in the
  enclosing repository. Used by ``.pre-commit-config.yaml`` with
  ``pass_filenames: false`` so editing the YAML config itself does not
  feed it as a target.
* Explicit positional args: scan those files only. Used by the unit
  tests for targeted exit-code coverage.

Exit codes:

* ``0`` -- clean (no violations).
* ``1`` -- one or more policy violations.
* ``2`` -- argv error from argparse.
* ``3`` -- I/O or parse error on at least one input file.
"""

import argparse
import ast
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

_DTO_MODULE_PREFIX: Final[str] = "synthorg.api.dto_"

_IO_ERROR_PREFIX: Final[str] = "[I/O ERROR] "

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

_SCOPED_DIRS: Final[tuple[Path, ...]] = (
    _REPO_ROOT / "src" / "synthorg" / "persistence",
    _REPO_ROOT / "src" / "synthorg" / "api" / "services",
)

# ``from synthorg.api import dto_capability`` reaches the same DTO
# module as ``from synthorg.api.dto_capability import ...`` but the
# imported *name* (not the module) carries the ``dto_`` prefix.
_API_PACKAGE: Final[str] = "synthorg.api"
_DTO_NAME_PREFIX: Final[str] = "dto_"


def _iter_import_violations(tree: ast.AST, path: Path) -> Iterable[str]:
    """Yield violation messages for each forbidden import in ``tree``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(_DTO_MODULE_PREFIX):
                names = ", ".join(alias.name for alias in node.names)
                yield (
                    f"{path}:{node.lineno}: forbidden import "
                    f"`from {module} import {names}` "
                    f"(persistence/service must not import api.dto_*)"
                )
            elif module == _API_PACKAGE:
                # ``from synthorg.api import dto_capability`` -- the
                # forbidden DTO is the imported name, not the module.
                for alias in node.names:
                    if alias.name.startswith(_DTO_NAME_PREFIX):
                        yield (
                            f"{path}:{node.lineno}: forbidden import "
                            f"`from {module} import {alias.name}` "
                            f"(persistence/service must not import api.dto_*)"
                        )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(_DTO_MODULE_PREFIX):
                    yield (
                        f"{path}:{node.lineno}: forbidden import "
                        f"`import {alias.name}` "
                        f"(persistence/service must not import api.dto_*)"
                    )


def _scan(path: Path) -> tuple[list[str], list[str]]:
    """Return (violations, io_errors) for ``path``.

    ``io_errors`` carries the ``_IO_ERROR_PREFIX`` sentinel so ``main``
    can map them to exit code 3 instead of conflating with policy
    violations.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [], [f"{_IO_ERROR_PREFIX}{path}: cannot read: {exc}"]
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [], [f"{_IO_ERROR_PREFIX}{path}: syntax error: {exc.msg}"]
    return list(_iter_import_violations(tree, path)), []


def _discover_default_paths() -> list[Path]:
    """Return all ``*.py`` files under ``src/synthorg/{persistence,service}/``."""
    discovered: list[Path] = []
    for root in _SCOPED_DIRS:
        if root.exists():
            discovered.extend(sorted(root.rglob("*.py")))
    return discovered


def main(argv: list[str] | None = None) -> int:
    """Run the gate; return process exit code (0/1/2/3)."""
    parser = argparse.ArgumentParser(
        description="forbid synthorg.api.dto_* imports in persistence/service layers"
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help=(
            "Python files to scan. When omitted, the gate scans every "
            "*.py file under src/synthorg/persistence/ and src/synthorg/service/."
        ),
    )
    args = parser.parse_args(argv)

    paths: list[Path] = list(args.paths) if args.paths else _discover_default_paths()

    all_violations: list[str] = []
    all_io_errors: list[str] = []
    for path in paths:
        violations, io_errors = _scan(path)
        all_violations.extend(violations)
        all_io_errors.extend(io_errors)

    for line in all_io_errors:
        print(line, file=sys.stderr)
    for line in all_violations:
        print(line, file=sys.stderr)

    if all_io_errors:
        return 3
    if all_violations:
        return 1
    return 0


def _run_cli() -> None:
    """Entry-point wrapper that converts ``main()`` return value to SystemExit."""
    rc = main(sys.argv[1:])
    raise SystemExit(rc)


if __name__ == "__main__":
    try:
        _run_cli()
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover -- last-resort safety net
        print(f"{_IO_ERROR_PREFIX}fatal: {exc}", file=sys.stderr)
        raise SystemExit(3) from exc
