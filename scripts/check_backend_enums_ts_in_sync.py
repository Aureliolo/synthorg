#!/usr/bin/env python3
"""Pre-push / CI gate: ``backend-enums.gen.ts`` matches its Python sources.

Re-runs ``scripts/generate_backend_enums_ts.py`` in ``--check`` mode so a
PR that adds a ``WsEventType`` / ``NotificationSeverity`` / ``LogLevel``
member on the backend without regenerating the frontend mirror cannot
land. Thin wrapper keeping orchestration uniform with the other
check_* gates.

Usage::

    uv run python scripts/check_backend_enums_ts_in_sync.py
"""

import subprocess
import sys
from pathlib import Path


def main() -> int:
    """Invoke the generator in check mode and propagate its exit code."""
    repo_root = Path(__file__).resolve().parents[1]
    generator = repo_root / "scripts" / "generate_backend_enums_ts.py"
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
