#!/usr/bin/env python3
"""PreToolUse hook: block shell-driven bulk-edit shortcuts.

The native Edit tool (including `replace_all: true`) and the Write tool
are intentionally NOT blocked: they produce a reviewable, atomic diff
the user sees and approves through the normal tool-permission flow.

Blocks only shell in-place bulk rewrites that bypass that per-diff
review:
  - `sed -i` / `sed --in-place` (also `gsed`)
  - `awk -i inplace` / `gawk -i inplace`
  - `perl -i` / `perl -pi` / `perl -pie`
  - shell-redirect overwrite of an existing tracked source file
    (`> path/foo.py`, `>> path/foo.md`) -- the common
    `echo ... > file` / piped-sed-output overwrite shortcut.

These shell forms apply sweeping changes with no diff surfaced for
approval, so they still require an explicit go-ahead. See the archived
`feedback_no_bulk_edit_without_approval` memory for the policy history.
"""

import json
import re
import sys

_BULK_BASH_PATTERNS = (
    # sed -i / sed --in-place (also gsed on macOS GNU-coreutils setups)
    re.compile(r"\b(?:sed|gsed)\s+(?:-[a-zA-Z]*i\b|--in-place\b)"),
    # awk / gawk with -i inplace; bound to a single command segment so an
    # unrelated awk earlier in a piped command does not falsely match.
    re.compile(r"\b(?:g?awk)\s+[^;|&]*\B-i\s+inplace\b"),
    # perl in-place: covers -i, -pi, -pie, -ne -i, plus bundled numeric
    # record-separator switches (-0pi, -0777i, -pi -e, etc.) that the
    # narrow form would miss.
    re.compile(
        r"\bperl\s+(?:-[a-zA-Z]*\d*[a-zA-Z]*i\b|-pi\b|-pie\b|-0\d*[a-zA-Z]*i\b)"
    ),
    # Stream-redirect overwrite of an existing tracked source file
    # (`> path/foo.py`, `>> path/foo.md`). 2> stderr redirection is
    # excluded by the negative lookbehind. Limited to known extensions
    # so that stdout redirection to /dev/null, sockets, or arbitrary
    # log files is not flagged as a bulk-edit.
    re.compile(
        r"(?<![0-9])>>?\s*[^/\s][^\s]*\.(?:py|md|ts|tsx|js|jsx|json|yaml|yml|toml|sh|go|css|astro)\b"
    ),
)


def _scan_bash(tool_input: object) -> int:
    """Return the exit code for a ``Bash`` tool envelope.

    A well-formed Bash envelope always carries an object ``tool_input``
    with a string ``command``. A non-dict tool_input or non-string
    command is a corrupted envelope for the path this gate inspects --
    fail closed (same rationale as the non-object payload guard) so it
    cannot crash with AttributeError/TypeError and silently bypass the
    gate. Returns 2 on a detected bulk edit or malformed Bash envelope,
    0 otherwise.
    """
    if not isinstance(tool_input, dict):
        print(
            "BLOCKED: malformed PreToolUse JSON envelope (Bash tool_input "
            f"is {type(tool_input).__name__}, expected object); "
            "check_no_bulk_edit fails closed.",
            file=sys.stderr,
        )
        return 2
    command = tool_input.get("command", "")
    if not isinstance(command, str):
        print(
            "BLOCKED: malformed PreToolUse JSON envelope (Bash command is "
            f"{type(command).__name__}, expected string); "
            "check_no_bulk_edit fails closed.",
            file=sys.stderr,
        )
        return 2
    for pattern in _BULK_BASH_PATTERNS:
        if pattern.search(command):
            print(
                "BLOCKED: detected an in-place bulk-edit shell command "
                f"({pattern.pattern}). Bulk edits require explicit user "
                "approval. Use the Edit tool with replace_all=false for "
                "per-occurrence edits, or ask the user to approve the "
                "bulk operation.",
                file=sys.stderr,
            )
            return 2
    return 0


def main() -> int:
    """Read the PreToolUse JSON envelope from stdin and decide whether to block."""
    raw = sys.stdin.read()
    if not raw.strip():
        # No stdin (pre-commit, not a tool call): not applicable, pass.
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        # stdin present but unparseable is an unknown state, not "no
        # opinion" -- fail closed so a corrupted/truncated envelope
        # cannot silently bypass the bulk-edit guard.
        print(
            f"BLOCKED: malformed PreToolUse JSON envelope ({exc}); "
            f"check_no_bulk_edit fails closed.",
            file=sys.stderr,
        )
        return 2

    # ``json.loads`` happily returns a list/str/number for a valid but
    # non-object payload; ``.get()`` below would then raise
    # ``AttributeError`` and the gate would crash instead of failing
    # closed. A non-dict envelope is just as malformed as unparseable
    # input -- treat it the same way.
    if not isinstance(payload, dict):
        print(
            "BLOCKED: malformed PreToolUse JSON envelope (expected object, "
            f"got {type(payload).__name__}); check_no_bulk_edit fails closed.",
            file=sys.stderr,
        )
        return 2

    tool_name = payload.get("tool_name", "")
    if tool_name == "Bash":
        return _scan_bash(payload.get("tool_input"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
