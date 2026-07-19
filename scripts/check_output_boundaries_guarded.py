#!/usr/bin/env python3
"""Reachability lock: every agent-output boundary routes through the guard.

The output-style policy only enforces if each agent-output boundary calls the
deterministic guard before the output escapes. This gate asserts that each known
boundary module still references an output-style enforcement entry point
(``enforce_output_policy`` or ``evaluate_output_policy``), so a refactor that
silently drops a guard at a boundary fails CI rather than shipping an
unenforced path. Complements the anti-ghost manifest, which locks that the
service is bound at boot.

Exit codes:

* 0: every boundary still calls a guard entry point
* 1: a boundary lost its guard (regression)
* 2: a boundary file is missing or unreadable (fail-closed setup error)

See CLAUDE.md "Agent Output-Style Policy (MANDATORY)".
"""

import sys
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

# The guard entry points any boundary may call (from engine/output_style).
_GUARD_SYMBOLS: Final[tuple[str, ...]] = (
    "enforce_output_policy",
    "evaluate_output_policy",
)

# Each agent-output boundary and the guard it must keep. Relative to src/.
_BOUNDARIES: Final[dict[str, str]] = {
    "src/synthorg/communication/messenger.py": "inter-agent message send",
    "src/synthorg/communication/messages/service.py": "MCP message send",
    "src/synthorg/tools/git_tools.py": "agent commit message",
    "src/synthorg/meta/appliers/code_applier.py": "agent PR title / body",
    "src/synthorg/engine/_review_oracle_gates.py": "completing deliverable",
}


def main() -> int:
    """Assert each boundary references a guard entry point.

    Returns:
        ``0`` when every boundary is guarded, ``1`` on a dropped guard, ``2``
        when a boundary file cannot be read (fail-closed).
    """
    unguarded: list[str] = []
    read_errors: list[str] = []
    for relative, label in _BOUNDARIES.items():
        path = _REPO_ROOT / relative
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            read_errors.append(f"{relative}: {exc}")
            continue
        if not any(symbol in source for symbol in _GUARD_SYMBOLS):
            unguarded.append(f"{relative} ({label})")

    if read_errors:
        print("ERROR: output-boundary gate could not read:", file=sys.stderr)
        for err in read_errors:
            print(f"  {err}", file=sys.stderr)
        return 2

    if unguarded:
        print(
            "Output-style guard missing at these agent-output boundaries "
            "(each must call enforce_output_policy / evaluate_output_policy "
            "before the output escapes):"
        )
        for entry in unguarded:
            print(f"  {entry}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
