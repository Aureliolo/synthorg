#!/usr/bin/env python3
"""Pre-push gate: forbid bare numeric claims in public docs.

Scans a fixed set of public documentation files for digit literals
adjacent to known stat nouns (``tests``, ``providers``, ``agents``,
``stars``, ``releases``) and stat keywords (``Mem0``, ``version``,
``release(d|s)``, ``current``, ``latest``). Any such literal must be
wrapped in ``<!--RS:NAME-->...<!--/RS-->`` markers driven by
``data/runtime_stats.yaml`` so the value can be regenerated at build
time, or carry a per-line opt-out comment::

    <!-- lint-allow: doc-numeric-macros -- <reason> -->

The opt-out marker requires a reason; bare ``<!-- lint-allow:
doc-numeric-macros -->`` does not suppress.

Files in scope are listed in :data:`_SCOPED_FILES`. Auto-generated
pages (``docs/reference/comparison.md``, ``docs/openapi/``,
``docs/api/``) are deliberately not scanned: their numeric content
flows from a different build-time source.

Exit codes:
    0 - no violations.
    1 - one or more violations printed to stderr.
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT = Path(__file__).resolve().parent.parent

_SCOPED_FILES: Final[tuple[str, ...]] = (
    "README.md",
    "docs/index.md",
    "docs/roadmap/index.md",
    "docs/architecture/decisions.md",
    "docs/reference/convention-gates.md",
)

_NUMBER: Final[str] = (
    r"\d{1,3}(?:[,.]\d{3})+\+?(?:[kKmM]\+?)?|\d{4,}\+?(?:[kKmM]\+?)?|\d{1,3}\+?(?:[kKmM]\+?)?"
)
_STAT_NOUN: Final[str] = r"(?:tests?|providers?|agents?|stars?|releases?)"
_KEYWORD: Final[str] = r"(?:Mem0|version|release[ds]?|current|latest)"

_NEAR_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    rf"\b({_NUMBER})\s+(?:[A-Za-z-]+\s+){{0,4}}({_STAT_NOUN})\b",
    re.IGNORECASE,
)
_NEAR_KEYWORD_RE: Final[re.Pattern[str]] = re.compile(
    rf"\b({_KEYWORD})\s+(v?\d[\d.,]*\+?[kKmM]?\+?)\b",
    re.IGNORECASE,
)
_MACRO_RE: Final[re.Pattern[str]] = re.compile(
    r"<!--RS:[a-z0-9_]+-->.*?<!--/RS-->", re.DOTALL
)
_INLINE_CODE_RE: Final[re.Pattern[str]] = re.compile(r"`[^`\n]*`")
_OPT_OUT_RE: Final[re.Pattern[str]] = re.compile(
    r"<!--\s*lint-allow:\s*doc-numeric-macros\s+--\s+\S+.*?-->"
)
_FENCE_OPEN_RE: Final[re.Pattern[str]] = re.compile(r"^\s*(?:```|~~~)")

_REMEDIATION_HINT: Final[str] = (
    "wrap in <!--RS:NAME-->VALUE<!--/RS--> with NAME defined in "
    "data/runtime_stats.yaml, or add "
    "<!-- lint-allow: doc-numeric-macros -- <reason> -->"
)


@dataclass(frozen=True)
class Violation:
    """A single bare-numeric-claim hit."""

    file_label: str
    lineno: int
    match: str

    def render(self) -> str:
        """Render as the standard pre-commit-style ``<file>:<line>: msg``."""
        return (
            f"{self.file_label}:{self.lineno}: bare numeric claim "
            f"{self.match!r} -- {_REMEDIATION_HINT}"
        )


def _strip_unscanned_regions(line: str) -> str:
    """Remove macroed regions and inline code spans from a line.

    The remaining text is what the regexes scan: anything still bare is
    a real violation.
    """
    return _INLINE_CODE_RE.sub("", _MACRO_RE.sub("", line))


def scan_text(text: str, *, file_label: str) -> list[str]:
    """Return human-readable violation messages for *text*.

    Multi-line macroed regions are stripped before per-line scanning so
    a marker that wraps content across newlines is still recognised.
    Lines inside fenced code blocks (``` or ~~~) are skipped entirely;
    inline code spans are stripped per-line. Lines carrying the per-line
    opt-out marker (with mandatory reason) are skipped.
    """
    pre_stripped = _MACRO_RE.sub("", text)
    in_fence = False
    violations: list[Violation] = []
    for lineno, raw_line in enumerate(pre_stripped.splitlines(), start=1):
        if _FENCE_OPEN_RE.match(raw_line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if _OPT_OUT_RE.search(raw_line):
            continue
        scanned = _strip_unscanned_regions(raw_line)
        for pattern in (_NEAR_NUMBER_RE, _NEAR_KEYWORD_RE):
            violations.extend(
                Violation(file_label=file_label, lineno=lineno, match=match.group(0))
                for match in pattern.finditer(scanned)
            )
    return [v.render() for v in violations]


def scan_file(rel_path: str) -> tuple[list[str], bool]:
    """Scan a single in-scope file. Returns ``(violations, was_present)``.

    Missing files are not violations: the gate's job is to flag bare
    literals where they exist, not to enforce the file inventory.
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
        print(
            f"warning: skipping missing scoped file {rel}",
            file=sys.stderr,
        )

    if all_violations:
        for line in all_violations:
            print(line, file=sys.stderr)
        print(
            f"\n{len(all_violations)} bare numeric claim(s) found in "
            f"{len(_SCOPED_FILES) - len(missing)} scanned file(s). "
            "Source the value from data/runtime_stats.yaml via "
            "<!--RS:NAME-->...<!--/RS--> markers, or add a per-line "
            "opt-out with a reason.",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: {len(_SCOPED_FILES) - len(missing)} scoped file(s) free of "
        "bare numeric claims."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
