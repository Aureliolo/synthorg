#!/usr/bin/env python3
"""Gate every frozen Pydantic model under ``src/synthorg/`` to forbid extras.

A frozen model that does not declare ``extra="forbid"`` silently
absorbs unknown construction keys, masking caller typos and letting
fabricated fields slip into business logic. ``CLAUDE.md`` section 8
makes ``extra="forbid"`` the project standard for every model that
does not need to round-trip through ``model_dump()``. This gate
enforces that statically and project-wide (it strictly supersedes the
old API-DTO-only ``check_dto_forbid_extra.py``).

Scope: every class under ``src/synthorg/`` whose OWN body assigns
``model_config = ConfigDict(...)`` (or a dict literal) with
``frozen=True``.

Carve-outs:

* **``@computed_field`` (automatic).** Pydantic v2 includes a
  computed field's value in ``model_dump()`` output; a strict-extra
  reconstruction would reject that key on the round trip, so models
  declaring a ``@computed_field`` are exempt without annotation. This
  is the section-8 documented carve-out, detected by AST so the ~68
  affected classes need no per-line noise.
* **Per-line opt-out.** ``# lint-allow: frozen-extra-forbid --
  <reason>`` on the class definition line, ``<reason>`` non-empty,
  for the genuine remaining exceptions (e.g. an ``extra="allow"``
  envelope that must accept arbitrary provider keys, or a
  validator-gated config that round-trips through ``model_dump``).
  Bare opt-outs without a reason are violations.

Exit codes:
    0 -- all frozen models forbid extras (or are carved out).
    1 -- one or more frozen models are missing ``extra="forbid"``.
    2 -- internal error parsing a source file.
"""

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src" / "synthorg"

_OPTOUT_WITH_REASON_RE = re.compile(
    r"#\s*lint-allow:\s*frozen-extra-forbid\s*--\s*(?P<reason>\S.*?)\s*$"
)
_OPTOUT_BARE_RE = re.compile(r"#\s*lint-allow:\s*frozen-extra-forbid\b")


def _config_value(node: ast.ClassDef) -> ast.Call | ast.Dict | None:
    """Return the final ``model_config`` ConfigDict/dict in the class body.

    Last-write-wins: a class cannot bypass the gate by setting a
    strict config early and overriding it later.
    """
    selected: ast.Call | ast.Dict | None = None
    for stmt in node.body:
        value: ast.expr | None = None
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == "model_config"
        ) or (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.target.id == "model_config"
        ):
            value = stmt.value
        if value is None:
            continue
        if isinstance(value, ast.Call):
            func = value.func
            if (isinstance(func, ast.Name) and func.id == "ConfigDict") or (
                isinstance(func, ast.Attribute) and func.attr == "ConfigDict"
            ):
                selected = value
        elif isinstance(value, ast.Dict):
            selected = value
    return selected


_MISSING: object = object()


def _config_flag(value: ast.Call | ast.Dict, name: str) -> object:
    """Return the literal value of config kwarg ``name`` or ``_MISSING``."""
    if isinstance(value, ast.Call):
        for kw in value.keywords:
            if kw.arg == name and isinstance(kw.value, ast.Constant):
                return kw.value.value
        return _MISSING
    for key, val in zip(value.keys, value.values, strict=False):
        if (
            isinstance(key, ast.Constant)
            and key.value == name
            and isinstance(val, ast.Constant)
        ):
            return val.value
    return _MISSING


def _has_computed_field(node: ast.ClassDef) -> bool:
    """True iff the class declares a ``@computed_field`` method/property."""
    for member in node.body:
        if not isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for dec in member.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            dec_name = (
                target.attr
                if isinstance(target, ast.Attribute)
                else (target.id if isinstance(target, ast.Name) else "")
            )
            if dec_name == "computed_field":
                return True
    return False


def _header_span(node: ast.ClassDef, total_lines: int) -> tuple[int, int]:
    """Return the inclusive 1-based line range of the class header.

    The header runs from ``class`` to the line before the first body
    statement. ``ruff format`` wraps a long ``class X(Base):`` plus a
    trailing ``# lint-allow`` comment across several lines, so the
    opt-out marker may land on the wrapped ``):`` line rather than
    ``node.lineno``; scanning the whole header span finds it either
    way.
    """
    start = node.lineno
    body_first = min(
        (child.lineno for child in node.body),
        default=start,
    )
    end = max(start, body_first - 1)
    return start, min(end, total_lines)


def _optout_status(
    source_lines: list[str],
    node: ast.ClassDef,
) -> str:
    """Return ``"with-reason"`` / ``"bare"`` / ``"none"`` for the header."""
    start, end = _header_span(node, len(source_lines))
    header_lines = source_lines[start - 1 : end]
    if any(_OPTOUT_WITH_REASON_RE.search(line) for line in header_lines):
        return "with-reason"
    if any(_OPTOUT_BARE_RE.search(line) for line in header_lines):
        return "bare"
    return "none"


def _walk(path: Path) -> list[tuple[Path, int, str]]:
    """Return ``(path, lineno, class_name)`` violations in ``path``."""
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
        cfg = _config_value(node)
        if cfg is None:
            continue
        if _config_flag(cfg, "frozen") is not True:
            continue
        if _config_flag(cfg, "extra") == "forbid":
            continue
        if _has_computed_field(node):
            # Section-8 documented carve-out: model_dump emits the
            # computed key; strict reconstruction would reject it.
            continue
        optout = _optout_status(source_lines, node)
        if optout == "with-reason":
            continue
        violations.append((path, node.lineno, node.name))
    return violations


def main() -> int:
    """Walk ``src/synthorg/`` and report frozen models without forbid."""
    if not SRC_DIR.is_dir():
        print(f"{SRC_DIR} does not exist", file=sys.stderr)
        return 2
    violations: list[tuple[Path, int, str]] = []
    for path in sorted(SRC_DIR.rglob("*.py")):
        violations.extend(_walk(path))
    if not violations:
        return 0
    print(
        f'{len(violations)} frozen model(s) missing extra="forbid":',
        file=sys.stderr,
    )
    for path, lineno, name in violations:
        rel = path.relative_to(REPO_ROOT)
        print(f"  {rel}:{lineno}  class {name}", file=sys.stderr)
    print(
        '\nAdd ``extra="forbid"`` to each frozen ConfigDict. A model '
        "that declares a @computed_field is auto-exempt. Genuine "
        "exceptions use a per-line opt-out: "
        "``# lint-allow: frozen-extra-forbid -- <reason>`` on the "
        "class definition line.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
