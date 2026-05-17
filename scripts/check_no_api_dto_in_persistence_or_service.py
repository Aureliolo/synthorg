#!/usr/bin/env python3
"""Pre-push / CI gate: forbid ``synthorg.api.dto_*`` imports in persistence and service layers.

The persistence and service layers must not depend on API-layer DTOs.
DTOs that need to be shared with the persistence layer live in the
domain module they describe (e.g.
``synthorg.providers.management.capability_dtos``); the ``api.dto_*``
modules are HTTP-facing aliases and importing them from the
persistence layer creates a layering cycle.

The gate scans:

* every ``*.py`` file under ``src/synthorg/persistence/``
* every ``*.py`` file under ``src/synthorg/api/services/`` (the
  API service layer)
* every domain service-layer module elsewhere under
  ``src/synthorg/`` -- files named ``service.py`` or matching
  ``*_service.py`` (e.g. ``hr/offboarding_service.py``,
  ``memory/service.py``) -- so a service module edited in isolation
  cannot slip an ``api.dto_*`` import past the gate

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

_SRC_ROOT: Final[Path] = _REPO_ROOT / "src" / "synthorg"

_SCOPED_DIRS: Final[tuple[Path, ...]] = (
    _SRC_ROOT / "persistence",
    _SRC_ROOT / "api" / "services",
)

# Domain service-layer modules live throughout the tree (not just
# under api/services/). They are identified by filename: ``service.py``
# or ``*_service.py``. Scanning them by glob keeps the gate's coverage
# aligned with the "service layer must not import api.dto_*" rule even
# when such a module is edited in isolation.
_SERVICE_MODULE_GLOBS: Final[tuple[str, ...]] = (
    "**/service.py",
    "**/*_service.py",
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
            is_relative = node.level > 0
            # ``from synthorg.api.dto_x import Y`` (absolute) or the
            # relative equivalent ``from ..api.dto_x import Y`` /
            # ``from ...dto_x import Y`` whose final component is a
            # ``dto_*`` module.
            final_component = module.rsplit(".", 1)[-1]
            module_targets_dto = module.startswith(_DTO_MODULE_PREFIX) or (
                is_relative and final_component.startswith(_DTO_NAME_PREFIX)
            )
            # ``from synthorg.api import dto_x`` (absolute) or the
            # relative ``from ..api import dto_x`` / ``from . import
            # dto_x`` where the imported *name* is the DTO module.
            module_is_api = module == _API_PACKAGE or (
                is_relative
                and (module == "api" or module.endswith(".api") or module == "")
            )
            if module_targets_dto:
                names = ", ".join(alias.name for alias in node.names)
                rel = "." * node.level
                yield (
                    f"{path}:{node.lineno}: forbidden import "
                    f"`from {rel}{module} import {names}` "
                    f"(persistence/service must not import api.dto_*)"
                )
            elif module_is_api:
                # The forbidden DTO is the imported name, not the module.
                for alias in node.names:
                    if alias.name.startswith(_DTO_NAME_PREFIX):
                        rel = "." * node.level
                        yield (
                            f"{path}:{node.lineno}: forbidden import "
                            f"`from {rel}{module} import {alias.name}` "
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
    """Return persistence + service-layer ``*.py`` files to scan.

    Covers every file under :data:`_SCOPED_DIRS` plus every domain
    service-layer module (``service.py`` / ``*_service.py``) anywhere
    under ``src/synthorg/``. Paths are de-duplicated (a file under
    ``api/services/`` may match both) and returned in stable order.
    """
    discovered: set[Path] = set()
    for root in _SCOPED_DIRS:
        if root.exists():
            discovered.update(root.rglob("*.py"))
    if _SRC_ROOT.exists():
        for pattern in _SERVICE_MODULE_GLOBS:
            discovered.update(_SRC_ROOT.glob(pattern))
    return sorted(discovered)


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
            "*.py file under src/synthorg/persistence/ and "
            "src/synthorg/api/services/, plus every domain service-layer "
            "module (service.py / *_service.py) under src/synthorg/."
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
