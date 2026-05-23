#!/usr/bin/env python3
"""Protocol-documented gate.

Every ``class Foo(Protocol):`` (or ``@runtime_checkable`` Protocol) in
``src/synthorg/`` must carry a non-trivial module-level docstring. The
gate's rationale: Protocol classes are the public surface of feature
boundaries. AI agents reading the codebase use Protocol docstrings to
understand callsite expectations; an undocumented Protocol forces a
grep walk every time.

Trivial docstring rules (any of the following fails):

* Empty docstring.
* Single placeholder token (``TODO``, ``TBD``, ``FIXME``, ``...``).
* Docstring shorter than 10 characters after strip.

Per-line opt-out with mandatory non-empty justification::

    class Foo(Protocol):  # lint-allow: protocol-doc -- vendored stub

Existing offenders absorbed via
``scripts/_protocol_doc_baseline.txt``. Each entry is
``path:lineno:ClassName``; the baseline shrinks monotonically.

Usage::

    uv run python scripts/check_protocol_documented.py
"""

import argparse
import ast
import dataclasses
import re
import sys
from pathlib import Path
from typing import Final

_REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
_BASELINE_REL = Path("scripts") / "_protocol_doc_baseline.txt"
_SCAN_REL: Final[str] = "src/synthorg"

_PROTOCOL_NAMES: Final[frozenset[str]] = frozenset({"Protocol", "typing.Protocol"})
_RUNTIME_CHECKABLE_NAMES: Final[frozenset[str]] = frozenset(
    {"runtime_checkable", "typing.runtime_checkable"}
)
_TRIVIAL_DOCSTRINGS: Final[frozenset[str]] = frozenset(
    {"todo", "tbd", "fixme", "...", "wip"}
)
_MIN_DOCSTRING_LENGTH: Final[int] = 10

_SUPPRESSION_RE: Final[re.Pattern[str]] = re.compile(
    r"\blint-allow:\s*protocol-doc\s*--\s*\S",
)

_BASELINE_LINE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<path>[^:#\s]+):(?P<line>\d+):(?P<name>\w+)\s*(?:#.*)?$"
)

_BASELINE_HEADER = (
    "# Frozen baseline of Protocol classes lacking a non-trivial docstring.\n"
    "# Each line is `path:lineno:ClassName` (POSIX path, 1-indexed line).\n"
    "# The gate suppresses these exact entries; new sites NOT listed fail.\n"
    "#\n"
    "# Regenerate (rare; requires explicit user approval) via the gate's\n"
    "# write_baseline() Python API.\n"
)


@dataclasses.dataclass(frozen=True)
class ProtocolFinding:
    """One Protocol class located in source."""

    path: str
    line: int
    name: str
    has_docstring: bool
    suppressed: bool

    def render(self) -> str:
        """Format for stderr / baseline: ``path:lineno:ClassName``."""
        return f"{self.path}:{self.line}:{self.name}"


def _docstring_is_non_trivial(docstring: str | None) -> bool:
    if docstring is None:
        return False
    cleaned = docstring.strip()
    if not cleaned:
        return False
    if cleaned.lower() in _TRIVIAL_DOCSTRINGS:
        return False
    return len(cleaned) >= _MIN_DOCSTRING_LENGTH


def _line_carries_suppression(line: str) -> bool:
    return bool(_SUPPRESSION_RE.search(line))


def _is_protocol_base(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id in _PROTOCOL_NAMES
    if isinstance(node, ast.Attribute):
        return f"{_attr_chain(node)}" in _PROTOCOL_NAMES
    return False


def _attr_chain(node: ast.Attribute) -> str:
    """Return the dotted attribute chain of *node* (e.g. ``typing.Protocol``)."""
    parts: list[str] = []
    current: ast.expr | None = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _has_runtime_checkable_decorator(
    decorators: list[ast.expr],
) -> bool:
    for deco in decorators:
        if isinstance(deco, ast.Name) and deco.id in _RUNTIME_CHECKABLE_NAMES:
            return True
        if (
            isinstance(deco, ast.Attribute)
            and _attr_chain(deco) in _RUNTIME_CHECKABLE_NAMES
        ):
            return True
        if (
            isinstance(deco, ast.Call)
            and isinstance(deco.func, ast.Name)
            and deco.func.id in _RUNTIME_CHECKABLE_NAMES
        ):
            return True
    return False


def find_protocols(path: Path) -> list[ProtocolFinding]:
    """Scan *path* and return every Protocol class definition.

    Includes both ``class Foo(Protocol):`` and ``@runtime_checkable``-
    decorated forms even when ``Protocol`` is not in the base list
    explicitly (matches the conventional case of `runtime_checkable`
    being only valid on a Protocol).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    findings: list[ProtocolFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        is_protocol = any(_is_protocol_base(base) for base in node.bases)
        is_runtime_checkable = _has_runtime_checkable_decorator(node.decorator_list)
        if not (is_protocol or is_runtime_checkable):
            continue
        docstring = ast.get_docstring(node)
        has_docstring = _docstring_is_non_trivial(docstring)
        class_line = lines[node.lineno - 1] if 0 <= node.lineno - 1 < len(lines) else ""
        suppressed = _line_carries_suppression(class_line)
        findings.append(
            ProtocolFinding(
                path=path.as_posix(),
                line=node.lineno,
                name=node.name,
                has_docstring=has_docstring,
                suppressed=suppressed,
            )
        )
    return findings


def _load_baseline(baseline_path: Path) -> set[str]:
    if not baseline_path.is_file():
        return set()
    entries: set[str] = set()
    for line in baseline_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _BASELINE_LINE_RE.match(stripped)
        if match is None:
            continue
        entries.add(
            f"{match.group('path')}:{match.group('line')}:{match.group('name')}"
        )
    return entries


def _iter_source_files(project_root: Path) -> list[Path]:
    scan_root = project_root / _SCAN_REL
    if not scan_root.is_dir():
        return []
    return sorted(scan_root.rglob("*.py"))


def check(*, project_root: Path, baseline_path: Path) -> list[ProtocolFinding]:
    """Return undocumented Protocol findings not absorbed by the baseline."""
    baseline = _load_baseline(baseline_path)
    out: list[ProtocolFinding] = []
    for path in _iter_source_files(project_root):
        rel_path = path.relative_to(project_root).as_posix()
        for finding in find_protocols(path):
            if finding.has_docstring or finding.suppressed:
                continue
            key = f"{rel_path}:{finding.line}:{finding.name}"
            if key in baseline:
                continue
            # Substitute path with rel-path for stable display
            out.append(
                ProtocolFinding(
                    path=rel_path,
                    line=finding.line,
                    name=finding.name,
                    has_docstring=finding.has_docstring,
                    suppressed=finding.suppressed,
                )
            )
    return out


def write_baseline(*, project_root: Path, baseline_path: Path) -> None:
    """Regenerate the baseline file from the current tree."""
    entries: list[str] = []
    for path in _iter_source_files(project_root):
        rel = path.relative_to(project_root).as_posix()
        for finding in find_protocols(path):
            if finding.has_docstring or finding.suppressed:
                continue
            entries.append(f"{rel}:{finding.line}:{finding.name}")
    entries.sort()
    body = "\n".join(entries)
    suffix = "\n" if body else ""
    baseline_path.write_text(_BASELINE_HEADER + body + suffix, encoding="utf-8")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=_REPO_ROOT_DEFAULT)
    parser.add_argument("--baseline", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = _build_arg_parser().parse_args(argv)
    project_root: Path = args.project_root.resolve()
    baseline_path: Path = (
        args.baseline.resolve()
        if args.baseline is not None
        else project_root / _BASELINE_REL
    )
    findings = check(project_root=project_root, baseline_path=baseline_path)
    if not findings:
        return 0
    print(
        "Protocol classes need a non-trivial docstring (>=10 chars, "
        "not just TODO/TBD/FIXME):",
        file=sys.stderr,
    )
    for finding in findings:
        print(f"  {finding.render()}", file=sys.stderr)
    print(
        "\nDocument the Protocol's contract or add a per-line "
        "`# lint-allow: protocol-doc -- <reason>` justification.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
