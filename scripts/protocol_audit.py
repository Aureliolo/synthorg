"""Audit-time helper: enumerate Protocol classes and collect usage counts.

Output: a CSV-shaped markdown table (one row per Protocol class):

    | path | line | name | rc | impl | typeuse | testuse |

Where:
    rc        - 1 if ``@runtime_checkable`` decorator on the class, else 0.
    impl      - count of explicit ``class X(<Name>...)`` matches in src/.
    typeuse   - count of ``: <Name> | -> <Name> | <Name> |`` matches in src/.
    testuse   - count of any ``<Name>`` token in tests/.

Run from the repo root:

    uv run python scripts/protocol_audit.py

The output is consumed by ``docs/reference/protocols-audit.md``; that
page's date pin in the title records when the snapshot was last
regenerated. Re-run when revisiting the audit.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "synthorg"
TESTS = ROOT / "tests"


@dataclass(frozen=True)
class ProtocolEntry:
    """One Protocol-class definition discovered during the SRC walk."""

    path: str
    line: int
    name: str
    runtime_checkable: bool


def _enumerate_protocols() -> list[ProtocolEntry]:
    """Walk SRC and yield every class declared as ``class X(... Protocol ...):``."""
    pattern = re.compile(
        r"^class (\w+)(?:\[[^\]]+\])?\((?:[\w\s,\.]*\b)Protocol\b",
    )
    rc_pattern = re.compile(r"^@runtime_checkable\s*$")
    entries: list[ProtocolEntry] = []
    for py_file in sorted(SRC.rglob("*.py")):
        try:
            text = py_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        lines = text.splitlines()
        rel = py_file.relative_to(ROOT).as_posix()
        for i, line in enumerate(lines):
            m = pattern.match(line)
            if not m:
                continue
            name = m.group(1)
            # Walk backwards through decorators / blank lines to detect
            # @runtime_checkable.
            j = i - 1
            rc = False
            while j >= 0 and lines[j].strip().startswith("@"):
                if rc_pattern.match(lines[j].strip()):
                    rc = True
                    break
                j -= 1
            entries.append(
                ProtocolEntry(
                    path=rel,
                    line=i + 1,
                    name=name,
                    runtime_checkable=rc,
                ),
            )
    return entries


def _count(pattern: str, root: Path) -> int:
    """Count matches via system ``grep -rEo``.

    The ``-o`` flag emits one line per match (rather than one line
    per matched file-line), so the splitlines-based counting below
    sees actual occurrence counts. Without it the helper would
    silently undercount any source line that contains multiple
    references to the same protocol (common in ``: <Name> | ->
    <Name>`` annotations).

    Raises ``RuntimeError`` on a missing grep binary or a non-zero
    grep failure exit code (>1). Silent-zero fallback would
    misclassify protocols as unused and quietly taint the audit
    table; the script is invoked manually so failing loudly is the
    right default.
    """
    try:
        result = subprocess.run(
            ["grep", "-rEo", "--include=*.py", pattern, str(root)],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        msg = (
            "grep binary not found on PATH; install GNU grep or run "
            "the audit on a system that ships it."
        )
        raise RuntimeError(msg) from exc
    if result.returncode > 1:
        msg = (
            f"grep exited with code {result.returncode} for pattern "
            f"{pattern!r} under {root}: {result.stderr.strip()}"
        )
        raise RuntimeError(msg)
    return sum(1 for line in result.stdout.splitlines() if line.strip())


def _impl_count(name: str) -> int:
    """Concrete classes inheriting from this Protocol (direct subclass)."""
    return _count(rf"^class \w+\(.*\b{name}\b", SRC)


def _typeuse_count(name: str) -> int:
    """Type-annotation use of the protocol name across SRC.

    Counts ``: <Name>``, ``-> <Name>``, and union-pipe ``| <Name>``
    occurrences. The defining ``class <Name>(`` line is already
    excluded by the pattern.
    """
    return _count(rf"(:|->|\|)\s*\b{name}\b", SRC)


def _testuse_count(name: str) -> int:
    """Count occurrences of the protocol name anywhere under tests/."""
    return _count(rf"\b{name}\b", TESTS)


def main() -> None:
    """Emit the Protocol-class audit table to stdout."""
    entries = _enumerate_protocols()
    print(f"# {len(entries)} Protocol classes")
    print()
    print("| path | line | name | rc | impl | typeuse | testuse |")
    print("|---|---|---|---|---|---|---|")
    for e in entries:
        impl = _impl_count(e.name)
        typeuse = _typeuse_count(e.name)
        testuse = _testuse_count(e.name)
        rc = "1" if e.runtime_checkable else "0"
        print(
            f"| {e.path} | {e.line} | {e.name} | {rc} | "
            f"{impl} | {typeuse} | {testuse} |",
        )


if __name__ == "__main__":
    main()
