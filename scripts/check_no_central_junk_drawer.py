#!/usr/bin/env python3
"""No-central-junk-drawer gate.

``src/synthorg/core/enums.py`` MUST NOT exist: enums live in their
per-feature ``enums.py`` module, never in a central file. Recreating the
central module fails the build.

The ``AppState`` slot invariant is owned by
``check_no_implicit_state_attribute.py``: its ``APPROVED_SLOTS`` is empty,
so any direct slot on the thin ``AppState.__slots__`` facade fails there.
This gate covers only the dissolved-module reconstruction guard.

Usage::

    uv run python scripts/check_no_central_junk_drawer.py
"""

import argparse
import sys
from pathlib import Path
from typing import Final

_REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent

_ENUMS_REL: Final[str] = "src/synthorg/core/enums.py"

# Dissolved junk-drawer modules that must never be recreated.
_MUST_NOT_EXIST: Final[tuple[str, ...]] = (_ENUMS_REL,)


def check_must_not_exist(*, project_root: Path) -> list[str]:
    """Return any dissolved junk-drawer paths that still exist."""
    return [rel for rel in _MUST_NOT_EXIST if (project_root / rel).is_file()]


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=_REPO_ROOT_DEFAULT,
        help="Override the project root.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. ``0`` clean, ``1`` if a dissolved module exists."""
    args = _build_arg_parser().parse_args(argv)
    project_root: Path = args.project_root.resolve()
    resurrected = check_must_not_exist(project_root=project_root)
    if not resurrected:
        return 0
    print("These dissolved junk-drawer modules must not exist:", file=sys.stderr)
    for rel in resurrected:
        print(f"  {rel}", file=sys.stderr)
    print(
        "\ncore/enums.py was dissolved into per-feature enums.py modules. "
        "Put new enums in their feature directory, not in a central file.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
