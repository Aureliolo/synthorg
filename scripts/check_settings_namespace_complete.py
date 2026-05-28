#!/usr/bin/env python3
"""Settings-namespace-completeness gate.

Every value declared in ``synthorg.settings.enums.SettingNamespace``
must have a matching ``src/synthorg/settings/definitions/<value>.py``
file containing that namespace's registered settings. A missing
definition means there's a settings namespace nobody can actually
write into. Catches the "added the enum, forgot the definitions file"
class of regression.

The gate parses ``settings/enums.py`` as text (via AST) to extract
StrEnum values, then matches each value against the file basenames in
``settings/definitions/``.

Existing offenders absorbed via
``scripts/_settings_namespace_baseline.txt`` (one namespace per line).

Usage::

    uv run python scripts/check_settings_namespace_complete.py
"""

import argparse
import ast
import dataclasses
import sys
from pathlib import Path
from typing import Final

_REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
_BASELINE_REL = Path("scripts") / "_settings_namespace_baseline.txt"
_ENUMS_REL: Final[str] = "src/synthorg/settings/enums.py"
_DEFINITIONS_REL: Final[str] = "src/synthorg/settings/definitions"
_ENUM_CLASS_NAME: Final[str] = "SettingNamespace"
_SKIP_DEFINITION_NAMES: Final[frozenset[str]] = frozenset({"__init__"})

_BASELINE_HEADER = (
    "# Frozen baseline of SettingNamespace values lacking a corresponding\n"
    "# settings/definitions/<name>.py file. One namespace per line.\n"
    "#\n"
    "# Regenerate (rare; requires explicit user approval) via the gate's\n"
    "# write_baseline() Python API.\n"
)


@dataclasses.dataclass(frozen=True)
class Finding:
    """A namespace declared in SettingNamespace without a definitions file."""

    namespace: str

    def render(self) -> str:
        """Format for stderr / baseline: ``<namespace>``."""
        return self.namespace


def extract_namespaces(project_root: Path) -> set[str]:
    """Return the set of namespace values from SettingNamespace, or empty."""
    enums_path = project_root / _ENUMS_REL
    if not enums_path.is_file():
        return set()
    try:
        tree = ast.parse(enums_path.read_text(encoding="utf-8"))
    except SyntaxError, OSError:
        return set()
    values: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != _ENUM_CLASS_NAME:
            continue
        for item in node.body:
            value = _extract_enum_value(item)
            if value is not None:
                values.add(value)
    return values


def _extract_enum_value(stmt: ast.stmt) -> str | None:
    if isinstance(stmt, (ast.AnnAssign, ast.Assign)):
        value = stmt.value
    else:
        return None
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def extract_definitions(project_root: Path) -> set[str]:
    """Return the set of feature names with a `definitions/<name>.py` file."""
    defs_dir = project_root / _DEFINITIONS_REL
    if not defs_dir.is_dir():
        return set()
    out: set[str] = set()
    for path in defs_dir.iterdir():
        if path.suffix != ".py":
            continue
        stem = path.stem
        if stem in _SKIP_DEFINITION_NAMES:
            continue
        out.add(stem)
    return out


def _load_baseline(baseline_path: Path) -> set[str]:
    if not baseline_path.is_file():
        return set()
    entries: set[str] = set()
    for line in baseline_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.add(stripped)
    return entries


def check(*, project_root: Path, baseline_path: Path) -> list[Finding]:
    """Run the gate against the project; return list of remaining findings."""
    namespaces = extract_namespaces(project_root)
    definitions = extract_definitions(project_root)
    baseline = _load_baseline(baseline_path)
    missing = sorted(namespaces - definitions)
    return [Finding(namespace=ns) for ns in missing if ns not in baseline]


def write_baseline(*, project_root: Path, baseline_path: Path) -> None:
    """Regenerate the baseline file from the current tree."""
    namespaces = extract_namespaces(project_root)
    definitions = extract_definitions(project_root)
    missing = sorted(namespaces - definitions)
    body = "\n".join(missing)
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
        "SettingNamespace values without a `settings/definitions/<name>.py`:",
        file=sys.stderr,
    )
    for finding in findings:
        print(f"  {finding.render()}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
