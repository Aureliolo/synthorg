#!/usr/bin/env python3
"""Pre-push / CI gate: ``error-codes.gen.ts`` matches ``error_taxonomy.py``.

Re-runs ``scripts/generate_error_codes_ts.py`` in ``--check`` mode so a
PR that adds an ``ErrorCode`` member on the backend without
regenerating the frontend mirror cannot land. The generator script
already supports the byte-comparison; this gate is a thin wrapper that
keeps the orchestration uniform with the other check_* gates.

Usage::

    uv run python scripts/check_error_codes_ts_in_sync.py
"""

import subprocess
import sys
from pathlib import Path


def main() -> int:
    """Invoke the generator in check mode and propagate its exit code."""
    repo_root = Path(__file__).resolve().parents[1]
    generator = repo_root / "scripts" / "generate_error_codes_ts.py"
    if not generator.exists():
        print(
            f"missing generator: {generator}",
            file=sys.stderr,
        )
        return 1
    completed = subprocess.run(
        [sys.executable, str(generator), "--check"],
        check=False,
        cwd=str(repo_root),
    )
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
