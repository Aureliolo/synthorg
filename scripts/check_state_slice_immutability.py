#!/usr/bin/env python3
"""State-slice-immutability gate.

State slices are per-feature frozen Pydantic models that decompose
``api/state.py``'s god-attribute-bag. Each slice must be FROZEN and
reject EXTRA fields so controllers cannot mutate state through the
slice handle.

The gate runs against today's tree with an empty baseline; new slices
introduced anywhere in ``src/synthorg/`` must satisfy the contract.

Detection: any class whose name ends in ``StateSlice`` OR whose base
list includes a name in :data:`_SLICE_BASE_NAMES` must declare::

    model_config = ConfigDict(frozen=True, extra="forbid")

Anything else (missing ``model_config``, ``frozen=False``,
``extra="allow"``/``"ignore"``, missing one of the keys) fails the gate.

Pre-existing offenders are absorbed via the frozen baseline at
``scripts/_state_slice_immutability_baseline.txt``.

Usage::

    uv run python scripts/check_state_slice_immutability.py
"""

import argparse
import ast
import dataclasses
import re
import sys
from pathlib import Path
from typing import Final

_REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
_BASELINE_REL = Path("scripts") / "_state_slice_immutability_baseline.txt"
_SCAN_REL: Final[str] = "src/synthorg"

_SLICE_NAME_SUFFIX: Final[str] = "StateSlice"
_SLICE_BASE_NAMES: Final[frozenset[str]] = frozenset({"BaseFeatureStateSlice"})

_BASELINE_LINE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<path>[^:#\s]+):(?P<line>\d+):(?P<name>\w+):(?P<reason>[\w_]+)\s*(?:#.*)?$"
)

_BASELINE_HEADER = (
    "# Frozen baseline of state-slice classes lacking frozen+extra=forbid.\n"
    "# Each line is `path:lineno:ClassName:reason`.\n"
    "# Empty in PR 1 (state slices land in PR 2).\n"
)


@dataclasses.dataclass(frozen=True)
class Finding:
    """One state-slice class violating the immutability contract."""

    path: str
    line: int
    name: str
    reason: str

    def render(self) -> str:
        """Format for stderr / baseline: ``path:lineno:Name:reason``."""
        return f"{self.path}:{self.line}:{self.name}:{self.reason}"


def _is_slice_class(node: ast.ClassDef) -> bool:
    if node.name.endswith(_SLICE_NAME_SUFFIX):
        return True
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id in _SLICE_BASE_NAMES:
            return True
        if isinstance(base, ast.Attribute) and base.attr in _SLICE_BASE_NAMES:
            return True
    return False


def _find_model_config_call(node: ast.ClassDef) -> ast.Call | None:
    """Return the ``ConfigDict(...)`` call assigned to ``model_config`` in *node*."""
    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign):
            target = stmt.target
            value = stmt.value
        elif isinstance(stmt, ast.Assign):
            target = stmt.targets[0] if len(stmt.targets) == 1 else None
            value = stmt.value
        else:
            continue
        if not isinstance(target, ast.Name) or target.id != "model_config":
            continue
        if isinstance(value, ast.Call):
            return value
    return None


def _config_value(call: ast.Call, key: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == key:
            return kw.value
    return None


def _violation_reason(node: ast.ClassDef) -> str | None:
    """Return a reason code if *node* violates the contract, else ``None``."""
    config_call = _find_model_config_call(node)
    if config_call is None:
        return "missing_model_config"
    frozen_node = _config_value(config_call, "frozen")
    if frozen_node is None:
        return "missing_frozen"
    if not (isinstance(frozen_node, ast.Constant) and frozen_node.value is True):
        return "frozen_not_true"
    extra_node = _config_value(config_call, "extra")
    if extra_node is None:
        return "missing_extra_forbid"
    if not (isinstance(extra_node, ast.Constant) and extra_node.value == "forbid"):
        return "extra_not_forbid"
    return None


def find_state_slice_issues(path: Path) -> list[Finding]:
    """Scan *path* and return every state-slice contract violation."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError, OSError:
        return []
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not _is_slice_class(node):
            continue
        reason = _violation_reason(node)
        if reason is None:
            continue
        findings.append(
            Finding(
                path=path.as_posix(),
                line=node.lineno,
                name=node.name,
                reason=reason,
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
                    match.group("name"),
                    match.group("reason"),
                )
            )
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
        for finding in find_state_slice_issues(path):
            key = f"{rel}:{finding.line}:{finding.name}:{finding.reason}"
            if key in baseline:
                continue
            out.append(
                Finding(
                    path=rel,
                    line=finding.line,
                    name=finding.name,
                    reason=finding.reason,
                )
            )
    return out


def write_baseline(*, project_root: Path, baseline_path: Path) -> None:
    """Regenerate the baseline file from the current tree."""
    entries: list[str] = []
    for path in _iter_source_files(project_root):
        rel = path.relative_to(project_root).as_posix()
        entries.extend(
            f"{rel}:{finding.line}:{finding.name}:{finding.reason}"
            for finding in find_state_slice_issues(path)
        )
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
        "State-slice classes must declare ConfigDict(frozen=True, extra='forbid'):",
        file=sys.stderr,
    )
    for finding in findings:
        print(f"  {finding.render()}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
