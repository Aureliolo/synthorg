#!/usr/bin/env python3
"""Reachability lock: every agent-output boundary routes through the guard.

The output-style policy only enforces if each agent-output boundary calls the
deterministic guard before the output escapes. This gate parses each known
boundary module and asserts it still CALLS its required guard entry point (an
AST call check, not a substring match, so a stray comment or dead import cannot
satisfy the lock). A refactor that silently drops a guard at a boundary fails CI
rather than shipping an unenforced path. Complements the anti-ghost manifest,
which locks that the service is bound at boot.

Exit codes:

* 0: every boundary still calls its guard entry point
* 1: a boundary lost its guard (regression)
* 2: a boundary file is missing, unreadable, or unparseable (fail-closed)

See CLAUDE.md "Output-Style Policy (MANDATORY)".
"""

import ast
import sys
from pathlib import Path
from typing import Final

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

# Each agent-output boundary, its label, and the guard call it must keep. The
# message-send facades route through the shared ``guard_message_output`` helper,
# which itself calls ``enforce_output_policy``; every other boundary calls a
# guard entry point directly. Paths are relative to the repo root.
_BOUNDARIES: Final[dict[str, tuple[str, frozenset[str]]]] = {
    "src/synthorg/communication/_output_guard.py": (
        "shared message guard",
        frozenset({"enforce_output_policy"}),
    ),
    "src/synthorg/communication/messenger.py": (
        "inter-agent message send",
        frozenset({"guard_message_output"}),
    ),
    "src/synthorg/communication/messages/service.py": (
        "MCP message send",
        frozenset({"guard_message_output"}),
    ),
    "src/synthorg/tools/git_tools.py": (
        "agent commit message",
        frozenset({"enforce_output_policy", "evaluate_output_policy"}),
    ),
    "src/synthorg/meta/appliers/code_applier.py": (
        "agent PR title / body",
        frozenset({"enforce_output_policy", "evaluate_output_policy"}),
    ),
    "src/synthorg/engine/_review_oracle_gates.py": (
        "completing deliverable",
        frozenset({"enforce_output_policy", "evaluate_output_policy"}),
    ),
    "src/synthorg/engine/initiative/evaluate_session.py": (
        "initiative evaluation verdict",
        frozenset({"enforce_output_policy", "evaluate_output_policy"}),
    ),
    "src/synthorg/tools/file_system/write_file.py": (
        "agent code-file write",
        frozenset({"enforce_output_policy", "evaluate_output_policy"}),
    ),
    "src/synthorg/tools/file_system/edit_file.py": (
        "agent code-file edit",
        frozenset({"enforce_output_policy", "evaluate_output_policy"}),
    ),
    "src/synthorg/tools/forge/forge_tools.py": (
        "agent issue / PR body",
        frozenset({"enforce_output_policy", "evaluate_output_policy"}),
    ),
    "src/synthorg/tools/_question_output_guard.py": (
        "shared parked-question guard",
        frozenset({"evaluate_output_policy"}),
    ),
    "src/synthorg/tools/clarification_tool.py": (
        "agent clarification question",
        frozenset({"guard_question_text"}),
    ),
    "src/synthorg/tools/decision_tool.py": (
        "agent decision question / options",
        frozenset({"guard_question_text"}),
    ),
}


def _called_names(tree: ast.AST) -> set[str]:
    """Collect the simple names invoked as calls anywhere in a module.

    Returns:
        The set of names appearing in ``f(...)`` and ``x.f(...)`` call
        positions, so a required guard must be genuinely called, not merely
        imported or mentioned in a comment/string.
    """
    called: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            called.add(func.id)
        elif isinstance(func, ast.Attribute):
            called.add(func.attr)
    return called


def main() -> int:
    """Assert each boundary calls its required guard entry point.

    Returns:
        ``0`` when every boundary is guarded, ``1`` on a dropped guard, ``2``
        when a boundary file cannot be read or parsed (fail-closed).
    """
    unguarded: list[str] = []
    read_errors: list[str] = []
    for relative, (label, required) in _BOUNDARIES.items():
        path = _REPO_ROOT / relative
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            read_errors.append(f"{relative}: {exc}")
            continue
        if not (required & _called_names(tree)):
            wanted = " / ".join(sorted(required))
            unguarded.append(f"{relative} ({label}); expected a call to {wanted}")

    if read_errors:
        print("ERROR: output-boundary gate could not read:", file=sys.stderr)
        for err in read_errors:
            print(f"  {err}", file=sys.stderr)
        return 2

    if unguarded:
        print(
            "Output-style guard missing at these agent-output boundaries "
            "(each must call its guard entry point before the output escapes):"
        )
        for entry in unguarded:
            print(f"  {entry}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
