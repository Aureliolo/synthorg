"""Gate: no placeholder / hardcoded model id as a per-feature model default.

A per-feature model must default to blank so an unconfigured feature reports
"not configured" and setup provisions a real model. A hardcoded default (the
``example-*`` placeholders especially, but any concrete model id) resolves at
runtime and silently 503s the feature against a model no provider serves.

This gate AST-scans two surfaces for a NON-blank string default on a
model-shaped name:

1. ``SettingDefinition(key="..._model", default="...")`` under
   ``src/synthorg/settings/definitions/``.
2. A Pydantic model field named ``model`` / ``*_model`` / ``*_model_id`` with a
   string-literal default (``Field(default="...")`` or ``name: T = "..."``)
   anywhere under ``src/synthorg/``.

Pre-existing offenders outside this policy's scope live in
``scripts/hardcoded_model_default_baseline.txt`` (``relpath:identifier`` per
line); the gate fails only on a NEW violation. Opt a genuine exception out with
a trailing ``# lint-allow: hardcoded-model-default -- <reason>`` on the field /
definition line.

Usage:
    uv run python scripts/check_no_hardcoded_model_default.py
    uv run python scripts/check_no_hardcoded_model_default.py --update-baseline

Exit codes:
    0 -- no new violations.
    1 -- a new hardcoded model default was found.
    2 -- configuration error (bad ``--repo-root`` or an unreadable source file).
"""

import argparse
import ast
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

_SRC_REL: Final[str] = "src/synthorg"
_DEFINITIONS_REL: Final[str] = "src/synthorg/settings/definitions"
_BASELINE_REL: Final[str] = "scripts/hardcoded_model_default_baseline.txt"
_MARKER: Final[str] = "lint-allow: hardcoded-model-default"


def _is_model_name(name: str) -> bool:
    """Whether *name* is a model-shaped field/key name."""
    return name == "model" or name.endswith(("_model", "_model_id"))


def _literal_str(value: ast.expr) -> str | None:
    """Return the string literal *value* names, unwrapping a wrapper call.

    Handles a bare ``"..."`` and a single-arg wrapper such as
    ``NotBlankStr("...")``.
    """
    if isinstance(value, ast.Constant):
        return value.value if isinstance(value.value, str) else None
    if (
        isinstance(value, ast.Call)
        and value.args
        and isinstance(value.args[0], ast.Constant)
        and isinstance(value.args[0].value, str)
    ):
        return value.args[0].value
    return None


def _string_default(value: ast.expr) -> str | None:
    """Return the non-blank string default of *value*, or ``None``.

    Handles a bare literal, a wrapper (``NotBlankStr("...")``), and a
    ``Field(default=...)`` / ``SettingDefinition(default=...)`` /
    ``Field("...")`` call (with the default itself possibly wrapped).
    """
    literal = _literal_str(value)
    if literal is not None:
        return literal or None
    if isinstance(value, ast.Call):
        for kw in value.keywords:
            if kw.arg == "default":
                inner = _literal_str(kw.value)
                if inner is not None:
                    return inner or None
        if value.args:
            inner = _literal_str(value.args[0])
            if inner is not None:
                return inner or None
    return None


def _line_allowed(text_lines: list[str], lineno: int) -> bool:
    """Whether the statement ending near *lineno* carries the opt-out marker."""
    # Scan a small window: a wrapped Field(...) spans a few lines.
    start = max(0, lineno - 1)
    end = min(len(text_lines), lineno + 8)
    return any(_MARKER in line for line in text_lines[start:end])


def _scan_setting_definitions(
    tree: ast.Module, text_lines: list[str], relpath: str
) -> list[str]:
    """Flag SettingDefinition(key="..._model", default="<non-blank>")."""
    findings: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "SettingDefinition"
        ):
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        key_node = kwargs.get("key")
        if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
            continue
        key = key_node.value
        if not _is_model_name(key):
            continue
        default_node = kwargs.get("default")
        if default_node is None:
            continue
        if _string_default(default_node) and not _line_allowed(text_lines, node.lineno):
            findings.append(f"{relpath}:setting:{key}")
    return findings


def _scan_config_fields(
    tree: ast.Module, text_lines: list[str], relpath: str
) -> list[str]:
    """Flag a class field named model/*_model with a non-blank string default."""
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            target: str | None = None
            value: ast.expr | None = None
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                target = stmt.target.id
                value = stmt.value
            elif (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
            ):
                target = stmt.targets[0].id
                value = stmt.value
            if target is None or value is None or not _is_model_name(target):
                continue
            if _string_default(value) and not _line_allowed(text_lines, stmt.lineno):
                findings.append(f"{relpath}:{node.name}.{target}")
    return findings


def _scan(root: Path) -> list[str]:
    """Return every current violation identifier under *root*.

    Raises:
        GateSourceError: When the expected source tree is missing under
            *root*, so a misconfigured ``--repo-root`` fails closed rather
            than silently scanning zero files and reporting no violations.
    """
    findings: list[str] = []
    definitions_dir = root / _DEFINITIONS_REL
    src_dir = root / _SRC_REL
    if not src_dir.is_dir():
        msg = f"expected source tree not found: {src_dir}"
        raise GateSourceError(msg)
    for path in sorted(src_dir.rglob("*.py")):
        relpath = path.relative_to(root).as_posix()
        text, tree = read_and_parse(path)
        lines = text.splitlines()
        findings.extend(_scan_config_fields(tree, lines, relpath))
        if definitions_dir in path.parents:
            findings.extend(_scan_setting_definitions(tree, lines, relpath))
    return findings


def _read_baseline(path: Path) -> set[str]:
    """Return the baselined violation identifiers (``{}`` when absent)."""
    if not path.is_file():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def main(argv: list[str] | None = None) -> int:
    """Scan for hardcoded model defaults and return the gate exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="Repository root.")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the baseline to the current violation set.",
    )
    args = parser.parse_args(argv)

    root = Path(args.repo_root).resolve()
    if not root.is_dir():
        print(f"error: --repo-root is not a directory: {root}", file=sys.stderr)
        return 2

    try:
        findings = set(_scan(root))
    except GateSourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    baseline_path = root / _BASELINE_REL
    if args.update_baseline:
        body = "\n".join(sorted(findings))
        header = (
            "# Pre-existing hardcoded model defaults, out of scope for the\n"
            "# no-hardcoded-model-default policy. The gate fails on a NEW entry.\n"
        )
        baseline_path.write_text(
            header + body + ("\n" if body else ""), encoding="utf-8"
        )
        print(f"wrote {len(findings)} entries to {_BASELINE_REL}")
        return 0

    baseline = _read_baseline(baseline_path)
    new = sorted(findings - baseline)
    if new:
        print(
            "error: hardcoded model default(s) found (must default to blank; "
            "setup provisions a real model):",
            file=sys.stderr,
        )
        for ident in new:
            print(f"  {ident}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
