#!/usr/bin/env python3
"""Regenerate the ``00000000000000_baseline.sql`` seed for each backend.

The baseline is a literal copy of ``persistence/<backend>/schema.sql``
prefixed with a short yoyo-friendly header.  When the canonical
schema is edited, this script re-flattens it so the seed stays in
sync with the declared state.

This script is the only sanctioned way to rewrite the baseline files;
the per-edit migration hook (`check_no_edit_migration.sh`) blocks any
other modifications, and the per-commit hook
(`check_no_modify_migration.sh`) blocks pushing the change unless
`SYNTHORG_MIGRATION_SQUASH=1` is set.

Usage::

    uv run python scripts/_regenerate_revisions_seed.py
    # or per backend:
    uv run python scripts/_regenerate_revisions_seed.py --backend sqlite

After running, commit with
``SYNTHORG_MIGRATION_SQUASH=1 git commit -m "..."``.
"""

import argparse
import sys
from pathlib import Path
from typing import Final, Literal

BackendName = Literal["sqlite", "postgres"]

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_BACKENDS: Final[tuple[BackendName, ...]] = ("sqlite", "postgres")

_HEADER_TEMPLATE: Final[str] = """\
-- {backend} schema seed.
--
-- This file is the canonical applied schema for SynthOrg's {backend}
-- persistence backend.  yoyo-migrations applies it to fresh databases
-- and tracks its content hash in the ``_yoyo_migration`` table; later
-- revisions live alongside as additional ``*.sql`` files in this
-- directory.
--
-- Do NOT hand-edit.  Regenerate via
-- ``scripts/_regenerate_revisions_seed.py`` after editing
-- ``../schema.sql``.
--
"""


def _schema_path(backend: BackendName) -> Path:
    return _REPO_ROOT / "src" / "synthorg" / "persistence" / backend / "schema.sql"


def _baseline_path(backend: BackendName) -> Path:
    return (
        _REPO_ROOT
        / "src"
        / "synthorg"
        / "persistence"
        / backend
        / "revisions"
        / "00000000000000_baseline.sql"
    )


def regenerate(backend: BackendName) -> Path:
    """Rewrite the baseline for *backend* from its schema.sql.

    Returns the absolute baseline path.
    """
    schema = _schema_path(backend).read_text(encoding="utf-8")
    header = _HEADER_TEMPLATE.format(backend=backend)
    target = _baseline_path(backend)
    target.write_text(header + schema, encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        choices=_BACKENDS,
        default=None,
        help="Regenerate only this backend's baseline (default: both).",
    )
    args = parser.parse_args(argv)
    targets: tuple[BackendName, ...] = (args.backend,) if args.backend else _BACKENDS
    for backend in targets:
        path = regenerate(backend)
        print(f"regenerated {path.relative_to(_REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
