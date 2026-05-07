#!/usr/bin/env python3
"""Gate every API-boundary DTO under ``src/synthorg/api/`` to forbid extras.

A DTO that does not declare ``extra="forbid"`` silently absorbs unknown
payload keys, which masks client typos and lets fabricated capability
flags slip through to handler logic. ``CLAUDE.md`` requires
``extra="forbid"`` on every Pydantic model that does not round-trip
through ``model_dump()``; this gate enforces that statically for every
class in ``src/synthorg/api/`` whose name ends with one of the
:data:`DTO_SUFFIXES` strings.

A class may declare a per-line opt-out by placing
``# lint-allow: dto-forbid-extra -- <reason>`` on the class definition
line, where ``<reason>`` is a non-empty justification. Bare opt-outs
without a reason are treated as violations.

Exit codes:
    0 -- all DTOs forbid extras (or no DTOs found).
    1 -- one or more DTOs are missing ``extra="forbid"``;
         offending sites printed to stderr.
    2 -- internal error parsing a source file.
"""

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_DIR = REPO_ROOT / "src" / "synthorg" / "api"

DTO_SUFFIXES: tuple[str, ...] = (
    "Request",
    "Response",
    "Snapshot",
    "Result",
    "Envelope",
    "Status",
    "Info",
    "Summary",
)

_OPTOUT_WITH_REASON_RE = re.compile(
    r"#\s*lint-allow:\s*dto-forbid-extra\s*--\s*(?P<reason>\S.*?)\s*$"
)
_OPTOUT_BARE_RE = re.compile(r"#\s*lint-allow:\s*dto-forbid-extra\b")


def _config_forbids_extras(value: ast.Call | ast.Dict) -> bool:
    """Return True iff a ``ConfigDict(...)`` or dict literal sets ``extra='forbid'``."""
    if isinstance(value, ast.Call):
        for kw in value.keywords:
            if kw.arg == "extra" and isinstance(kw.value, ast.Constant):
                return kw.value.value == "forbid"
        return False
    for key, val in zip(value.keys, value.values, strict=False):
        if (
            isinstance(key, ast.Constant)
            and key.value == "extra"
            and isinstance(val, ast.Constant)
        ):
            return val.value == "forbid"
    return False


def _model_config_assignment_value(stmt: ast.stmt) -> ast.expr | None:
    """Return the RHS of ``model_config = ...`` or ``model_config: T = ...``."""
    if (
        isinstance(stmt, ast.Assign)
        and len(stmt.targets) == 1
        and isinstance(stmt.targets[0], ast.Name)
        and stmt.targets[0].id == "model_config"
    ):
        return stmt.value
    if (
        isinstance(stmt, ast.AnnAssign)
        and isinstance(stmt.target, ast.Name)
        and stmt.target.id == "model_config"
    ):
        return stmt.value
    return None


def _model_config_value(node: ast.ClassDef) -> ast.Call | ast.Dict | None:
    """Return the ``model_config`` AST value (``ConfigDict(...)`` or ``{...}``)."""
    for stmt in node.body:
        value = _model_config_assignment_value(stmt)
        if value is None:
            continue
        if isinstance(value, ast.Call):
            func = value.func
            if (isinstance(func, ast.Name) and func.id == "ConfigDict") or (
                isinstance(func, ast.Attribute) and func.attr == "ConfigDict"
            ):
                return value
        if isinstance(value, ast.Dict):
            return value
    return None


def _base_name(base: ast.expr) -> str | None:
    """Return the base class's bare name (handles ``Name``/``Attribute``/``Subscript``)."""
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    if isinstance(base, ast.Subscript):
        return _base_name(base.value)
    return None


def _classes_in_module(tree: ast.AST) -> dict[str, ast.ClassDef]:
    """Index ``ast.ClassDef`` nodes by class name for ancestry lookup."""
    return {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}


def _has_basemodel_ancestor(
    node: ast.ClassDef,
    classes: dict[str, ast.ClassDef],
    visited: set[str] | None = None,
) -> bool:
    """Recursively check whether ``node`` ultimately inherits from ``BaseModel``.

    Only resolves base classes defined within the same source file
    (``classes`` index). A cross-file base whose own name does not end
    with one of :data:`DTO_SUFFIXES` cannot be resolved and is treated
    as *not* a ``BaseModel`` descendant; such DTOs will escape the gate
    unless they carry their own ``model_config`` assignment (as done
    for ``CreatePresetRequest``/``UpdatePresetRequest`` in
    ``dto_personalities.py``).
    """
    if visited is None:
        visited = set()
    if node.name in visited:
        return False
    visited.add(node.name)
    for base in node.bases:
        name = _base_name(base)
        if name is None:
            continue
        if name == "BaseModel":
            return True
        if name.endswith(DTO_SUFFIXES):
            return True
        parent = classes.get(name)
        if parent is not None and _has_basemodel_ancestor(parent, classes, visited):
            return True
    return False


def _is_dto_to_check(node: ast.ClassDef, classes: dict[str, ast.ClassDef]) -> bool:
    """Class name ends with a DTO suffix and inherits transitively from BaseModel."""
    if not node.name.endswith(DTO_SUFFIXES):
        return False
    return _has_basemodel_ancestor(node, classes)


def _line_optout_status(source_lines: list[str], lineno: int) -> str:
    """Return ``"with-reason"`` / ``"bare"`` / ``"none"`` for the class line.

    Returns ``"with-reason"`` for a valid ``# lint-allow: dto-forbid-extra
    -- <reason>`` exemption, ``"bare"`` for a malformed bare opt-out (which
    must be reported as a violation per the gate's contract), and
    ``"none"`` when no opt-out marker is present.
    """
    if not 1 <= lineno <= len(source_lines):
        return "none"
    line = source_lines[lineno - 1]
    if _OPTOUT_WITH_REASON_RE.search(line):
        return "with-reason"
    if _OPTOUT_BARE_RE.search(line):
        return "bare"
    return "none"


def _walk(path: Path) -> list[tuple[Path, int, str]]:
    """Return list of ``(path, lineno, class_name)`` violations in ``path``."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        print(f"{path}: failed to parse -- {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    source_lines = source.splitlines()
    classes = _classes_in_module(tree)
    violations: list[tuple[Path, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not _is_dto_to_check(node, classes):
            continue
        optout = _line_optout_status(source_lines, node.lineno)
        if optout == "with-reason":
            continue
        if optout == "bare":
            violations.append((path, node.lineno, node.name))
            continue
        config_value = _model_config_value(node)
        if config_value is None:
            violations.append((path, node.lineno, node.name))
            continue
        if not _config_forbids_extras(config_value):
            violations.append((path, node.lineno, node.name))
    return violations


def main() -> int:
    """Walk ``src/synthorg/api/`` and report any DTO without forbid."""
    if not API_DIR.is_dir():
        print(f"{API_DIR} does not exist", file=sys.stderr)
        return 2
    violations: list[tuple[Path, int, str]] = []
    for path in sorted(API_DIR.rglob("*.py")):
        violations.extend(_walk(path))
    if not violations:
        return 0
    suffix_list = ", ".join(f"*{s}" for s in DTO_SUFFIXES)
    print(
        f'{len(violations)} DTO(s) missing extra="forbid" in ConfigDict '
        f"(checked suffixes: {suffix_list}):",
        file=sys.stderr,
    )
    for path, lineno, name in violations:
        rel = path.relative_to(REPO_ROOT)
        print(f"  {rel}:{lineno}  class {name}", file=sys.stderr)
    print(
        '\nAdd ``extra="forbid"`` to each ConfigDict so the API boundary '
        "rejects unknown fields. Per-line opt-out: "
        "``# lint-allow: dto-forbid-extra -- <reason>`` on the class line.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
