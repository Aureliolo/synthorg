#!/usr/bin/env python3
"""Gate every request DTO under ``src/synthorg/api/`` to forbid extras.

Walks the AST of each ``*.py`` file in ``src/synthorg/api/`` and any
``Request``-suffixed Pydantic ``BaseModel`` subclass it finds, then
confirms its ``model_config`` literal contains the keyword argument
``extra="forbid"``.

The audit (``31-model-convention-violations``) caught 23 request DTOs
that silently accepted unknown payload keys.  Without a static gate, a
fresh request DTO would re-introduce the same surface immediately.

Exit codes:
    0 -- all request DTOs forbid extras (or no DTOs found).
    1 -- one or more request DTOs are missing ``extra="forbid"``;
         offending sites printed to stderr.
    2 -- internal error parsing a source file (bug in this script
         or a syntax error in the target file).
"""

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_DIR = REPO_ROOT / "src" / "synthorg" / "api"


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


def _is_request_dto(node: ast.ClassDef) -> bool:
    """Class name ends in ``Request`` and inherits from a BaseModel-shaped name."""
    if not node.name.endswith("Request"):
        return False
    for base in node.bases:
        # Accept ``BaseModel`` or any name suffixed Request/Response that
        # itself inherits from BaseModel; we resolve by name only since
        # the AST cannot follow imports.
        if isinstance(base, ast.Name) and base.id == "BaseModel":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "BaseModel":
            return True
    # Some request DTOs subclass another local request DTO that already
    # forbids extras; the gate still requires the leaf to repeat the
    # config so review can scan one file.
    return any(
        isinstance(base, ast.Name) and base.id.endswith(("Request", "RequestBase"))
        for base in node.bases
    )


def _walk(path: Path) -> list[tuple[Path, int, str]]:
    """Return list of ``(path, lineno, class_name)`` violations in ``path``."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        print(f"{path}: failed to parse -- {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    violations: list[tuple[Path, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not _is_request_dto(node):
            continue
        config_call = _model_config_call(node)
        if config_call is None:
            violations.append((path, node.lineno, node.name))
            continue
        if not _config_forbids_extras(config_call):
            violations.append((path, node.lineno, node.name))
    return violations


def main() -> int:
    """Walk ``src/synthorg/api/`` and report any request DTO without forbid."""
    if not API_DIR.is_dir():
        print(f"{API_DIR} does not exist", file=sys.stderr)
        return 2
    violations: list[tuple[Path, int, str]] = []
    for path in sorted(API_DIR.rglob("*.py")):
        violations.extend(_walk(path))
    if not violations:
        return 0
    print(
        f'{len(violations)} request DTO(s) missing extra="forbid" in ConfigDict:',
        file=sys.stderr,
    )
    for path, lineno, name in violations:
        rel = path.relative_to(REPO_ROOT)
        print(f"  {rel}:{lineno}  class {name}", file=sys.stderr)
    print(
        '\nAdd ``extra="forbid"`` to each ConfigDict so the API boundary '
        "rejects unknown fields (audit 31).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
