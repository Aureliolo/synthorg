#!/usr/bin/env python3
"""No-module-level-I/O gate.

Module import-time I/O (``open(...)``, network calls, subprocess
launches) makes the codebase fragile: the side-effect happens before
test fixtures are wired, before settings are resolved, before the
clock seam is injected. Worse, it makes the boot order load-bearing.

This gate walks module-body statements (and the bodies of module-level
``if`` / ``try`` / ``with`` blocks) and flags any call to a known I/O
function. Calls inside function/class bodies are exempt (intentional
lazy I/O). Calls inside ``if __name__ == "__main__":`` are exempt
(script entry points, never reached on import).

Forbidden call shapes:

* ``open(...)`` (builtin file I/O).
* ``subprocess.run(...)``, ``Popen(...)``, ``call(...)``,
  ``check_output(...)``, ``check_call(...)``.
* ``socket.socket(...)``, ``socket.create_connection(...)``.
* ``urllib.request.urlopen(...)``.
* ``requests.{get,post,put,delete,head,patch}(...)``.
* ``httpx.{get,post,put,delete,head,patch,Client,AsyncClient}(...)``.
* ``Path(...).read_text(...)``, ``read_bytes()``, ``write_text()``,
  ``write_bytes()``, ``open()``.

Per-line opt-out::

    open(...)  # lint-allow: module-io -- pre-load the cache at import

Existing offenders absorbed via
``scripts/_module_level_io_baseline.txt``; entries are
``path:lineno:funcname``.

Usage::

    uv run python scripts/check_no_module_level_io.py
"""

import argparse
import ast
import dataclasses
import re
import sys
from pathlib import Path
from typing import Final

_REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
_BASELINE_REL = Path("scripts") / "_module_level_io_baseline.txt"
_SCAN_REL: Final[str] = "src/synthorg"

_FORBIDDEN_BARE_NAMES: Final[frozenset[str]] = frozenset({"open"})

_FORBIDDEN_QUALIFIED: Final[frozenset[str]] = frozenset(
    {
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_output",
        "subprocess.check_call",
        "socket.socket",
        "socket.create_connection",
        "urllib.request.urlopen",
        "requests.get",
        "requests.post",
        "requests.put",
        "requests.delete",
        "requests.head",
        "requests.patch",
        "httpx.get",
        "httpx.post",
        "httpx.put",
        "httpx.delete",
        "httpx.head",
        "httpx.patch",
        "httpx.Client",
        "httpx.AsyncClient",
    }
)

_FORBIDDEN_METHODS: Final[frozenset[str]] = frozenset(
    {"read_text", "read_bytes", "write_text", "write_bytes"}
)

_SUPPRESSION_RE: Final[re.Pattern[str]] = re.compile(
    r"\blint-allow:\s*module-io\s*--\s*\S",
)

_BASELINE_LINE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<path>[^:#\s]+):(?P<line>\d+):(?P<func>[\w.]+)\s*(?:#.*)?$"
)

_BASELINE_HEADER = (
    "# Frozen baseline of module-import-time I/O calls.\n"
    "# Each line is `path:lineno:funcname` (POSIX path, 1-indexed line).\n"
    "#\n"
    "# Regenerate (rare; requires explicit user approval) via the gate's\n"
    "# write_baseline() Python API.\n"
)


@dataclasses.dataclass(frozen=True)
class Finding:
    """One module-level I/O call site."""

    path: str
    line: int
    funcname: str
    suppressed: bool

    def render(self) -> str:
        """Format for stderr / baseline: ``path:lineno:funcname``."""
        return f"{self.path}:{self.line}:{self.funcname}"


def _attr_chain(node: ast.expr) -> str:
    parts: list[str] = []
    current: ast.expr | None = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _resolve_call_name(call: ast.Call) -> str | None:
    """Return the dotted function name of a Call, or ``None``."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return _attr_chain(call.func)
    return None


def _is_forbidden_call(name: str) -> bool:
    if name in _FORBIDDEN_BARE_NAMES:
        return True
    if name in _FORBIDDEN_QUALIFIED:
        return True
    tail = name.rsplit(".", maxsplit=1)[-1] if "." in name else name
    return tail in _FORBIDDEN_METHODS


def _line_carries_suppression(line: str) -> bool:
    return bool(_SUPPRESSION_RE.search(line))


def _is_main_block(stmt: ast.stmt) -> bool:
    """Return True iff *stmt* is ``if __name__ == "__main__":``.

    The equality check is explicit: ``if __name__ != "__main__":`` does
    NOT count as a main guard, so its body keeps being scanned for
    forbidden module-import-time I/O.
    """
    if not isinstance(stmt, ast.If):
        return False
    test = stmt.test
    if not isinstance(test, ast.Compare):
        return False
    return (
        isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "__main__"
    )


def _collect_calls_pruned(node: ast.AST, findings: list[ast.Call]) -> None:
    """Collect ``ast.Call`` descendants reachable at module scope.

    Stops at ``FunctionDef`` / ``AsyncFunctionDef`` / ``ClassDef`` /
    ``Lambda`` so a top-level ``if`` that wraps a nested function
    definition does not flag the function's body as module-scope I/O.

    For a ``__main__`` guard the body is skipped (script entry point,
    never reached on import) but ``node.orelse`` still executes on
    import, so its else / elif branches are traversed normally.
    """
    if isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
    ):
        return
    if isinstance(node, ast.If) and _is_main_block(node):
        for stmt in node.orelse:
            _collect_calls_pruned(stmt, findings)
        return
    if isinstance(node, ast.Call):
        findings.append(node)
    for child in ast.iter_child_nodes(node):
        _collect_calls_pruned(child, findings)


def _walk_module_body(body: list[ast.stmt], findings: list[ast.Call]) -> None:
    """Collect Call nodes reachable at module scope."""
    for stmt in body:
        _collect_calls_pruned(stmt, findings)


def find_module_io(path: Path) -> list[Finding]:
    """Scan *path* and return every module-level I/O call site."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    calls: list[ast.Call] = []
    _walk_module_body(tree.body, calls)
    out: list[Finding] = []
    for call in calls:
        name = _resolve_call_name(call)
        if name is None or not _is_forbidden_call(name):
            continue
        call_line = lines[call.lineno - 1] if 0 <= call.lineno - 1 < len(lines) else ""
        out.append(
            Finding(
                path=path.as_posix(),
                line=call.lineno,
                funcname=name,
                suppressed=_line_carries_suppression(call_line),
            )
        )
    return out


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
            f"{match.group('path')}:{match.group('line')}:{match.group('func')}"
        )
    return entries


def _iter_source_files(project_root: Path) -> list[Path]:
    scan_root = project_root / _SCAN_REL
    if not scan_root.is_dir():
        return []
    return sorted(scan_root.rglob("*.py"))


def check(*, project_root: Path, baseline_path: Path) -> list[Finding]:
    """Run the gate against the project; return list of remaining findings."""
    baseline = _load_baseline(baseline_path)
    out: list[Finding] = []
    for path in _iter_source_files(project_root):
        rel = path.relative_to(project_root).as_posix()
        for finding in find_module_io(path):
            if finding.suppressed:
                continue
            key = f"{rel}:{finding.line}:{finding.funcname}"
            if key in baseline:
                continue
            out.append(
                Finding(
                    path=rel,
                    line=finding.line,
                    funcname=finding.funcname,
                    suppressed=finding.suppressed,
                )
            )
    return out


def write_baseline(*, project_root: Path, baseline_path: Path) -> None:
    """Regenerate the baseline file from the current tree."""
    entries: list[str] = []
    for path in _iter_source_files(project_root):
        rel = path.relative_to(project_root).as_posix()
        for finding in find_module_io(path):
            if finding.suppressed:
                continue
            entries.append(f"{rel}:{finding.line}:{finding.funcname}")
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
    """CLI entry point. Returns 0 on clean tree, 1 on any violation."""
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
        "Module-import-time I/O detected (move into a function or main block):",
        file=sys.stderr,
    )
    for finding in findings:
        print(f"  {finding.render()}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
