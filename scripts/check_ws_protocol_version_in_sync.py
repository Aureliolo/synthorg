#!/usr/bin/env python3
"""Pre-push / CI gate: ``WS_PROTOCOL_VERSION`` matches between Python and TypeScript.

The dashboard's WebSocket client validates every incoming event's
``version`` field against ``WS_PROTOCOL_VERSION`` in
``web/src/utils/constants.ts`` and discards mismatched frames. The
server emits frames with ``WS_PROTOCOL_VERSION`` from
``src/synthorg/api/ws_models.py``. If a PR bumps one side without the
other, the dashboard will silently discard every event after the next
deploy. This gate fails the build before that lands.

Usage::

    uv run python scripts/check_ws_protocol_version_in_sync.py
"""

import re
import sys
from pathlib import Path

PY_PATTERN = re.compile(
    r"^WS_PROTOCOL_VERSION\s*:\s*int\s*=\s*(\d+)\s*$",
    re.MULTILINE,
)
TS_PATTERN = re.compile(
    # Accept optional ``: <type>`` annotation and an optional trailing
    # semicolon so all valid TypeScript declarations of the constant
    # parse cleanly:
    #   export const WS_PROTOCOL_VERSION = 7
    #   export const WS_PROTOCOL_VERSION = 7;
    #   export const WS_PROTOCOL_VERSION: number = 7;
    r"^export\s+const\s+WS_PROTOCOL_VERSION(?:\s*:\s*[^=]+)?\s*=\s*(\d+)\s*;?\s*$",
    re.MULTILINE,
)


def _read_int(path: Path, pattern: re.Pattern[str], label: str) -> int | None:
    if not path.exists():
        print(f"missing {label}: {path}", file=sys.stderr)
        return None
    contents = path.read_text(encoding="utf-8")
    match = pattern.search(contents)
    if match is None:
        print(
            f"could not find WS_PROTOCOL_VERSION declaration in {label}: {path}",
            file=sys.stderr,
        )
        return None
    return int(match.group(1))


def main() -> int:
    """Compare both declarations and exit 0 when they agree."""
    repo_root = Path(__file__).resolve().parents[1]
    py_path = repo_root / "src" / "synthorg" / "api" / "ws_models.py"
    ts_path = repo_root / "web" / "src" / "utils" / "constants.ts"

    py_version = _read_int(py_path, PY_PATTERN, "Python")
    ts_version = _read_int(ts_path, TS_PATTERN, "TypeScript")

    if py_version is None or ts_version is None:
        return 1

    if py_version != ts_version:
        print(
            f"WS_PROTOCOL_VERSION drift: Python={py_version} TypeScript={ts_version}",
            file=sys.stderr,
        )
        print(
            f"  python: {py_path.relative_to(repo_root)}",
            file=sys.stderr,
        )
        print(
            f"  typescript: {ts_path.relative_to(repo_root)}",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
