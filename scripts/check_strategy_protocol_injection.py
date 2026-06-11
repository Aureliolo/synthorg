#!/usr/bin/env python3
"""Strategy-protocol-injection gate.

When a class is registered with a factory / registry, it is a strategy
plugin. Callsites SHOULD type their parameter as the corresponding
Protocol, not the concrete class. Concrete-impl annotations defeat
dependency inversion: they couple the consumer to the specific impl
and prevent swapping via configuration.

Detection (heuristic; a richer architectural-feedback variant may
supersede this once it lands):

1. Walk ``src/synthorg/`` for calls matching
   ``<Anything>.register(...)`` / ``register_strategy(...)`` /
   ``<Registry>.register_<verb>(...)`` whose arguments include a
   Name referencing a class. Collect the (file, class_name) pairs.
2. Walk every other file. For each function parameter or return
   annotation that is an ``ast.Name`` matching a registered class,
   emit a violation. The file that did the registration is exempt
   (it is allowed to reference its concrete impl directly).
3. Baseline absorbs current violations; new ones fail.

Per-line opt-out:

    def use(foo: ConcreteFoo) -> None:  # lint-allow: strategy-protocol -- <reason>

Usage::

    uv run python scripts/check_strategy_protocol_injection.py
"""

import argparse
import ast
import dataclasses
import re
import sys
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import (  # type: ignore[import-not-found]
        GateSourceError,
        read_and_parse,
    )
else:
    from scripts._gate_source import GateSourceError, read_and_parse

_REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
_BASELINE_REL = Path("scripts") / "_strategy_protocol_injection_baseline.txt"
_SCAN_REL: Final[str] = "src/synthorg"

_REGISTER_CALL_PATTERNS: Final[frozenset[str]] = frozenset(
    {"register", "register_strategy", "add_strategy", "register_backend"}
)

_SUPPRESSION_RE: Final[re.Pattern[str]] = re.compile(
    r"\blint-allow:\s*strategy-protocol\s*--\s*\S",
)

_BASELINE_LINE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<path>[^:#\s]+):(?P<line>\d+):(?P<func>\w+):(?P<cls>\w+)\s*(?:#.*)?$"
)

_BASELINE_HEADER = (
    "# Frozen baseline of callsites annotating with a factory-registered\n"
    "# concrete strategy class instead of its Protocol.\n"
    "# Each line is `path:lineno:funcname:ConcreteClass`.\n"
    "#\n"
    "# Regenerate (rare; requires explicit user approval) via the gate's\n"
    "# write_baseline() Python API.\n"
)


@dataclasses.dataclass(frozen=True)
class RegisteredClass:
    """A concrete class registered via a factory call."""

    factory_path: str
    class_name: str


@dataclasses.dataclass(frozen=True)
class Finding:
    """One callsite annotating with a registered concrete class."""

    path: str
    line: int
    funcname: str
    class_name: str
    suppressed: bool

    def render(self) -> str:
        """Format for stderr / baseline: ``path:lineno:funcname:Class``."""
        return f"{self.path}:{self.line}:{self.funcname}:{self.class_name}"


def _iter_source_files(project_root: Path) -> list[Path]:
    scan_root = project_root / _SCAN_REL
    if not scan_root.is_dir():
        return []
    return sorted(scan_root.rglob("*.py"))


def _resolve_call_funcname(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def harvest_registered_classes(project_root: Path) -> list[RegisteredClass]:
    """Return every ``RegisteredClass`` reachable from factory-style calls.

    Raises:
        GateSourceError: If a source file cannot be read or parsed
            (fail-closed: a skipped factory file would hide its registered
            classes and silently exempt their callsites).
    """
    out: list[RegisteredClass] = []
    for path in _iter_source_files(project_root):
        _, tree = read_and_parse(path)
        rel = path.relative_to(project_root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            funcname = _resolve_call_funcname(node)
            if funcname is None or funcname not in _REGISTER_CALL_PATTERNS:
                continue
            out.extend(
                RegisteredClass(factory_path=rel, class_name=arg.id)
                for arg in node.args
                if isinstance(arg, ast.Name)
            )
    return out


def _annotation_names(annotation: ast.expr | None) -> set[str]:
    """Return every bare ``Name`` reachable inside *annotation*.

    Walking the annotation subtree lets the gate flag concrete strategy
    classes that are buried inside Union (``ConcreteFoo | None``),
    Optional, or generic containers (``list[ConcreteFoo]``) -- a plain
    ``isinstance(annotation, ast.Name)`` check would only catch the
    bare-annotation case.
    """
    if annotation is None:
        return set()
    return {node.id for node in ast.walk(annotation) if isinstance(node, ast.Name)}


def _line_carries_suppression(line: str) -> bool:
    return bool(_SUPPRESSION_RE.search(line))


def _line_for(lineno: int | None, lines: list[str]) -> str:
    if lineno is None:
        return ""
    idx = lineno - 1
    if 0 <= idx < len(lines):
        return lines[idx]
    return ""


def _scan_callsites(
    path: Path,
    project_root: Path,
    registered_by_class: dict[str, set[str]],
) -> list[Finding]:
    """Return callsites in *path* using a registered class as annotation.

    Args:
        path: The source file to scan.
        project_root: Project root used to compute rel-paths.
        registered_by_class: ``{class_name: {factory_path, ...}}``. A
            callsite is exempt if its containing file appears in the
            factory-path set for the registered class.

    Scans every parameter slot (positional-only, regular, keyword-only)
    and the function return annotation. A concrete class buried inside
    Union / Optional / generic containers is still reported because
    :func:`_annotation_names` walks the annotation subtree.

    Raises:
        GateSourceError: If *path* cannot be read or parsed (fail-closed).
    """
    text, tree = read_and_parse(path)
    rel = path.relative_to(project_root).as_posix()
    lines = text.splitlines()
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for arg in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ):
            arg_line = _line_for(arg.lineno, lines)
            arg_suppressed = _line_carries_suppression(arg_line)
            for class_name in _annotation_names(arg.annotation):
                if class_name not in registered_by_class:
                    continue
                if rel in registered_by_class[class_name]:
                    continue
                findings.append(
                    Finding(
                        path=rel,
                        line=arg.lineno or node.lineno,
                        funcname=node.name,
                        class_name=class_name,
                        suppressed=arg_suppressed,
                    )
                )
        return_anno = node.returns
        if return_anno is not None:
            return_line = _line_for(return_anno.lineno, lines)
            if not return_line:
                return_line = _line_for(node.lineno, lines)
            return_suppressed = _line_carries_suppression(return_line)
            for class_name in _annotation_names(return_anno):
                if class_name not in registered_by_class:
                    continue
                if rel in registered_by_class[class_name]:
                    continue
                findings.append(
                    Finding(
                        path=rel,
                        line=return_anno.lineno or node.lineno,
                        funcname=node.name,
                        class_name=class_name,
                        suppressed=return_suppressed,
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
            ":".join(
                (
                    match.group("path"),
                    match.group("line"),
                    match.group("func"),
                    match.group("cls"),
                )
            )
        )
    return entries


def check(*, project_root: Path, baseline_path: Path) -> list[Finding]:
    """Run the gate against the project; return list of remaining findings."""
    registered = harvest_registered_classes(project_root)
    if not registered:
        return []
    by_class: dict[str, set[str]] = {}
    for entry in registered:
        by_class.setdefault(entry.class_name, set()).add(entry.factory_path)
    baseline = _load_baseline(baseline_path)
    out: list[Finding] = []
    for path in _iter_source_files(project_root):
        for finding in _scan_callsites(path, project_root, by_class):
            if finding.suppressed:
                continue
            key = finding.render()
            if key in baseline:
                continue
            out.append(finding)
    return out


def write_baseline(*, project_root: Path, baseline_path: Path) -> None:
    """Regenerate the baseline file from the current tree."""
    registered = harvest_registered_classes(project_root)
    by_class: dict[str, set[str]] = {}
    for entry in registered:
        by_class.setdefault(entry.class_name, set()).add(entry.factory_path)
    entries: list[str] = []
    for path in _iter_source_files(project_root):
        for finding in _scan_callsites(path, project_root, by_class):
            if finding.suppressed:
                continue
            entries.append(finding.render())
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
    try:
        findings = check(project_root=project_root, baseline_path=baseline_path)
    except GateSourceError as exc:
        print(
            f"FAIL (strategy-injection scan could not read a file): {exc}",
            file=sys.stderr,
        )
        return 2
    if not findings:
        return 0
    print(
        "Callsites annotate with factory-registered CONCRETE classes "
        "instead of their Protocol. Use the Protocol type at the boundary:",
        file=sys.stderr,
    )
    for finding in findings:
        print(f"  {finding.render()}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
