#!/usr/bin/env python3
"""Pre-push / CI gate: ``web/src/api/types/*.gen.ts`` matches Pydantic DTOs.

Re-runs ``scripts/generate_dto_types_ts.py --check`` so a PR that
edits a Pydantic DTO without regenerating the TypeScript mirrors
cannot land. The generator already does byte-comparison across all
three ``.gen.ts`` outputs; this wrapper keeps the orchestration
uniform with the other ``check_*.py`` gates.

Usage::

    uv run python scripts/check_dto_types_ts_in_sync.py
"""

import subprocess
import sys
from pathlib import Path


def main() -> int:
    """Invoke the generator in check mode and propagate its exit code."""
    repo_root = Path(__file__).resolve().parents[1]
    generator = repo_root / "scripts" / "generate_dto_types_ts.py"
    if not generator.exists():
        print(f"missing generator: {generator}", file=sys.stderr)
        return 1
    completed = subprocess.run(
        [sys.executable, str(generator), "--check"],
        check=False,
        cwd=str(repo_root),
    )
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
