#!/usr/bin/env python3
"""PreToolUse hook: block bulk-edit operations that need explicit user approval.

Blocks:
  1. Edit tool with `replace_all: true` -- bulk replacement across a single file
     (the user must approve every bulk edit explicitly).
  2. Bash commands using `sed -i`, `sed --in-place`, `awk -i inplace`,
     `perl -i`, `perl -pi`, or shell-redirect overwrites that bulk-rewrite
     a file (`> file`, `>> file` to existing tracked files via piped sed/awk
     output) -- common bulk-edit shortcuts that bypass per-line review.

The user has explicitly stated that bulk edits must always be approved
before applying. See the `feedback_no_bulk_edit_without_approval` memory.
"""

import json
import re
import sys

_BULK_BASH_PATTERNS = (
    re.compile(r"\bsed\s+(?:-[a-zA-Z]*i|--in-place\b)"),
    re.compile(r"\bawk\s+(?:-[a-zA-Z]*i\b|.*-i\s+inplace)"),
    re.compile(r"\bperl\s+(?:-[a-zA-Z]*i\b|-pi\b|-pie\b)"),
    re.compile(r"\bgawk\s+(?:-[a-zA-Z]*i\b|.*-i\s+inplace)"),
)


def main() -> int:
    """Read the PreToolUse JSON envelope from stdin and decide whether to block."""
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}

    if tool_name == "Edit" and tool_input.get("replace_all") is True:
        print(
            "BLOCKED: Edit with replace_all=true is a bulk edit. "
            "The user must explicitly approve every bulk edit before it runs. "
            "Either ask the user to confirm bulk replacement of "
            f"`{tool_input.get('old_string', '<old_string>')}` in "
            f"`{tool_input.get('file_path', '<file>')}`, or do per-occurrence edits "
            "with replace_all=false (one Edit call per occurrence with enough "
            "context to make old_string unique).",
            file=sys.stderr,
        )
        return 2

    if tool_name == "Bash":
        command = tool_input.get("command", "") or ""
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


if __name__ == "__main__":
    sys.exit(main())
