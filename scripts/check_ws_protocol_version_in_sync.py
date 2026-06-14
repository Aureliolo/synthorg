#!/usr/bin/env python3
"""Pre-push / CI gate: WebSocket lockstep constants match between Python and TS.

The dashboard's WebSocket client validates every incoming event's
``version`` field against ``WS_PROTOCOL_VERSION`` in
``web/src/utils/ws-constants.ts`` and discards mismatched frames, and it
caps inbound event size at ``WS_MAX_MESSAGE_SIZE``. The server emits
frames stamped with ``WS_PROTOCOL_VERSION`` from
``src/synthorg/api/ws_models.py`` and refuses to emit any event larger
than ``_MAX_OUTBOUND_EVENT_BYTES`` in
``src/synthorg/api/controllers/ws.py``. If a PR bumps one side without
the other, the dashboard silently discards every event (version drift)
or the two ends disagree on the frame-size ceiling (size drift) after
the next deploy. This gate fails the build before that lands.

Only the constants that have a genuine backend counterpart are checked
here. The remaining client-side timing knobs documented in
``ws-constants.ts`` (``WS_HEARTBEAT_INTERVAL_MS``, ``WS_PONG_TIMEOUT_MS``,
``LOG_SANITIZE_MAX_LENGTH``) have no backend constant to lock against --
they govern client-side heartbeat / dead-socket detection and client log
truncation -- so there is nothing to compare them to.

Usage::

    uv run python scripts/check_ws_protocol_version_in_sync.py
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Numeric literals on either side may carry digit-group underscores
# (e.g. ``32_768``); capture them and strip before ``int()``.
_NUM = r"([0-9][0-9_]*)"


@dataclass(frozen=True)
class _SyncedConstant:
    """One lockstep constant: its TS name + the backend file and name."""

    ts_name: str
    py_relpath: str
    py_name: str

    def py_pattern(self) -> re.Pattern[str]:
        """Return the Python declaration regex for this constant."""
        return re.compile(
            rf"^{re.escape(self.py_name)}(?:\s*:\s*[^=]+)?\s*=\s*{_NUM}\s*$",
            re.MULTILINE,
        )

    def ts_pattern(self) -> re.Pattern[str]:
        """Return the TypeScript declaration regex for this constant."""
        return re.compile(
            rf"^export\s+const\s+{re.escape(self.ts_name)}"
            rf"(?:\s*:\s*[^=]+)?\s*=\s*{_NUM}\s*;?\s*$",
            re.MULTILINE,
        )


_TS_RELPATH = "web/src/utils/ws-constants.ts"

_SYNCED_CONSTANTS: tuple[_SyncedConstant, ...] = (
    _SyncedConstant(
        ts_name="WS_PROTOCOL_VERSION",
        py_relpath="src/synthorg/api/ws_models.py",
        py_name="WS_PROTOCOL_VERSION",
    ),
    _SyncedConstant(
        ts_name="WS_MAX_MESSAGE_SIZE",
        py_relpath="src/synthorg/api/controllers/ws.py",
        py_name="_MAX_OUTBOUND_EVENT_BYTES",
    ),
)


def _read_int(
    path: Path,
    pattern: re.Pattern[str],
    name: str,
    label: str,
) -> int | None:
    """Return the integer value of *name* in *path*, or ``None`` if absent."""
    if not path.exists():
        print(f"missing {label}: {path}", file=sys.stderr)
        return None
    contents = path.read_text(encoding="utf-8")
    match = pattern.search(contents)
    if match is None:
        print(
            f"could not find {name} declaration in {label}: {path}",
            file=sys.stderr,
        )
        return None
    return int(match.group(1).replace("_", ""))


def _check_constant(repo_root: Path, spec: _SyncedConstant) -> bool:
    """Return True if *spec* agrees across Python and TypeScript."""
    py_path = repo_root / spec.py_relpath
    ts_path = repo_root / _TS_RELPATH

    py_value = _read_int(py_path, spec.py_pattern(), spec.py_name, "Python")
    ts_value = _read_int(ts_path, spec.ts_pattern(), spec.ts_name, "TypeScript")

    if py_value is None or ts_value is None:
        return False

    if py_value != ts_value:
        print(
            f"{spec.ts_name} drift: "
            f"Python({spec.py_name})={py_value} "
            f"TypeScript({spec.ts_name})={ts_value}",
            file=sys.stderr,
        )
        print(f"  python: {spec.py_relpath}", file=sys.stderr)
        print(f"  typescript: {_TS_RELPATH}", file=sys.stderr)
        return False

    return True


def main() -> int:
    """Compare every lockstep constant and exit 0 when they all agree."""
    repo_root = Path(__file__).resolve().parents[1]
    # Evaluate every spec (no short-circuit) so all drifts are reported
    # in one run rather than just the first.
    results = [_check_constant(repo_root, spec) for spec in _SYNCED_CONSTANTS]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
