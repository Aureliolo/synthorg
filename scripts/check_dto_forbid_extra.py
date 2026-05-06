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

_OPTOUT_RE = re.compile(
    r"#\s*lint-allow:\s*dto-forbid-extra\s*--\s*(?P<reason>\S.*?)\s*$"
)


def _config_forbids_extras(call: ast.Call) -> bool:
    """Return True iff a ``ConfigDict(...)`` call has ``extra='forbid'``."""
    for kw in call.keywords:
        if kw.arg == "extra" and isinstance(kw.value, ast.Constant):
            return kw.value.value == "forbid"
    return False


def _model_config_call(node: ast.ClassDef) -> ast.Call | None:
    """Return the ``ConfigDict(...)`` AST node bound to ``model_config``."""
    for stmt in node.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if not (
            len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == "model_config"
        ):
            continue
        if (
            isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Name)
            and stmt.value.func.id == "ConfigDict"
        ):
            return stmt.value
    return None


def _is_dto_to_check(node: ast.ClassDef) -> bool:
    """Class name ends with a DTO suffix and inherits from a BaseModel-shaped base."""
    if not node.name.endswith(DTO_SUFFIXES):
        return False
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == "BaseModel":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "BaseModel":
            return True
        # Generic envelope: ``BaseModel[T]`` parses as a Subscript.
        if isinstance(base, ast.Subscript):
            value = base.value
            if isinstance(value, ast.Name) and value.id == "BaseModel":
                return True
            if isinstance(value, ast.Attribute) and value.attr == "BaseModel":
                return True
    # Some DTOs subclass another local DTO that already forbids extras;
    # the gate still requires the leaf to repeat the config so review
    # can scan one file.
    return any(
        isinstance(base, ast.Name) and base.id.endswith(DTO_SUFFIXES)
        for base in node.bases
    )


def _line_has_optout(source_lines: list[str], lineno: int) -> bool:
    """Return True iff the class definition line carries a valid opt-out."""
    if not 1 <= lineno <= len(source_lines):
        return False
    return bool(_OPTOUT_RE.search(source_lines[lineno - 1]))


def _walk(path: Path) -> list[tuple[Path, int, str]]:
    """Return list of ``(path, lineno, class_name)`` violations in ``path``."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        print(f"{path}: failed to parse -- {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    source_lines = source.splitlines()
    violations: list[tuple[Path, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not _is_dto_to_check(node):
            continue
        if _line_has_optout(source_lines, node.lineno):
            continue
        config_call = _model_config_call(node)
        if config_call is None:
            violations.append((path, node.lineno, node.name))
            continue
        if not _config_forbids_extras(config_call):
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
