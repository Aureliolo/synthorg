#!/usr/bin/env python3
"""Gate: docs must not tell contributors to run ``pre-commit install``.

The project wires Git hooks through a *relative* ``core.hooksPath`` set
by ``scripts/install_git_hooks.sh`` (see ``docs/getting_started.md``).
Running ``pre-commit install`` instead writes venv-baked wrappers into
``.git/hooks/`` and rewrites the shared ``core.hooksPath`` to an absolute
path, which silently disables hook gating for EVERY worktree (the
absolute path resolves to one bare hooks dir that other worktrees never
populate). A single contributor following a stale setup instruction
breaks gating repo-wide with no error.

This gate scans the setup-instruction docs for any ``pre-commit install``
mention and fails unless the line also discourages it (a warning /
rejection, not a recommendation). The sanctioned setup command is
``bash scripts/install_git_hooks.sh``.

Scope is the fixed :data:`_SCOPED_FILES` tuple -- the docs where a setup
instruction actually lives. Adding a new setup doc is a deliberate edit
to that tuple, mirroring ``check_doc_numeric_macros.py``.

Exit codes:
    0 - no recommending mention found.
    1 - one or more recommending mentions printed to stderr.
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT = Path(__file__).resolve().parent.parent

_SCOPED_FILES: Final[tuple[str, ...]] = (
    ".github/CONTRIBUTING.md",
    "docs/getting_started.md",
    "README.md",
    "CLAUDE.md",
    "web/CLAUDE.md",
    "cli/CLAUDE.md",
    "docs/architecture/decisions.md",
)

_FORBIDDEN: Final[str] = "pre-commit install"

# U+2019 RIGHT SINGLE QUOTATION MARK. Normalised to the ASCII apostrophe
# before cue matching so the curly-apostrophe spelling of "don't" still
# matches the straight-apostrophe cue.
_CURLY_APOSTROPHE: Final[str] = chr(0x2019)

# A line may mention ``pre-commit install`` only if it also discourages
# running it. Cues are matched case-insensitively as whole words/phrases
# (regex word boundaries), so "Do not run", "(NOT ...)", "rejected",
# "no longer", and "never" all count. A bare substring match would let a
# recommending line slip through whenever a cue appears inside an
# unrelated word ("not" in "Note" / "cannot" / "annotation"), so the
# boundary check is what keeps the gate from passing such lines.
_DISCOURAGEMENT_CUES: Final[tuple[str, ...]] = (
    "not",
    "never",
    "no longer",
    "rejected",
    "don't",
)

# Pre-compiled word-boundary patterns for the cues above. Apostrophes are
# normalised to the straight form before matching (see ``scan_text``) so
# the curly-apostrophe spelling of "don't" still matches.
_DISCOURAGEMENT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(rf"\b{re.escape(cue)}\b") for cue in _DISCOURAGEMENT_CUES
)

_REMEDIATION_HINT: Final[str] = (
    "use `bash scripts/install_git_hooks.sh` (wires the relative "
    "core.hooksPath); `pre-commit install` clobbers it and disables hook "
    "gating across all worktrees. If the mention is a deliberate warning, "
    "phrase it so the line discourages running the command "
    "(e.g. 'do not run')."
)


@dataclass(frozen=True)
class Violation:
    """A line that recommends running ``pre-commit install``."""

    file_label: str
    lineno: int
    line: str

    def render(self) -> str:
        """Render as ``<file>:<line>: <offending text> -- <hint>``."""
        return (
            f"{self.file_label}:{self.lineno}: {self.line!r} recommends "
            f"`pre-commit install` -- {_REMEDIATION_HINT}"
        )


def scan_text(text: str, *, file_label: str) -> list[str]:
    """Return violation messages for every recommending mention in *text*.

    A line is a violation when it contains ``pre-commit install`` but no
    discouragement cue (whole-word/phrase match, case-insensitive).
    """
    violations: list[Violation] = []
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        lowered = raw_line.lower().replace(_CURLY_APOSTROPHE, "'")
        if _FORBIDDEN not in lowered:
            continue
        if any(pattern.search(lowered) for pattern in _DISCOURAGEMENT_PATTERNS):
            continue
        violations.append(
            Violation(file_label=file_label, lineno=lineno, line=raw_line.strip())
        )
    return [v.render() for v in violations]


def scan_file(rel_path: str) -> tuple[list[str], bool]:
    """Scan one in-scope file. Returns ``(violations, was_present)``.

    Missing files are not violations: the gate flags recommending
    mentions where they exist, it does not enforce the file inventory.
    """
    abs_path = REPO_ROOT / rel_path
    if not abs_path.is_file():
        return [], False
    text = abs_path.read_text(encoding="utf-8")
    return scan_text(text, file_label=rel_path), True


def main() -> int:
    """Iterate scoped files, print violations, return shell exit code."""
    all_violations: list[str] = []
    missing: list[str] = []
    for rel in _SCOPED_FILES:
        violations, was_present = scan_file(rel)
        if not was_present:
            missing.append(rel)
            continue
        all_violations.extend(violations)

    for rel in missing:
        print(f"warning: skipping missing scoped file {rel}", file=sys.stderr)

    if all_violations:
        for line in all_violations:
            print(line, file=sys.stderr)
        print(
            f"\n{len(all_violations)} doc(s) recommend running "
            "`pre-commit install`. The sanctioned setup command is "
            "`bash scripts/install_git_hooks.sh`.",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: {len(_SCOPED_FILES) - len(missing)} scoped doc(s) free of "
        "`pre-commit install` recommendations."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
