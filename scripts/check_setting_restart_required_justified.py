#!/usr/bin/env python3
"""Pre-push / CI gate: restart-required settings must be justified.

A setting flagged ``restart_required=True`` (or ``read_only_post_init=True``,
which implies it) is invisible to the settings-change dispatcher -- an operator
edit takes no runtime effect. The #2514 audit found the overwhelming majority of
such flags were immutable by omission, not by necessity. This gate stops the
codebase from silently defaulting NEW settings to restart-required: every
restart-bound definition must either

* carry a per-line ``# lint-allow: restart-required -- <reason>`` marker on its
  ``_r.register(...)`` block (a deliberate, justified keep), or
* be listed in the baseline ``scripts/setting_restart_required_baseline.txt``
  (the genuine OS/transport-bound keeps plus the namespaces deferred to the
  sibling conversion issues #2515 / #2516).

Lint behaviour: pass when every restart-bound setting is marked or baselined;
fail when a new unjustified restart-bound setting appears; warn (but pass) when
a baseline entry is stale (the setting was converted / removed). Regenerate the
baseline (rare; explicit user approval) with ``--update-baseline``.

Usage::

    python scripts/check_setting_restart_required_justified.py
    python scripts/check_setting_restart_required_justified.py --repo-root /path
    python scripts/check_setting_restart_required_justified.py --update-baseline
"""

import argparse
import ast
import io
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_MARKER: Final[str] = "lint-allow: restart-required"
_BASELINE_NAME: Final[str] = "setting_restart_required_baseline.txt"
_DEFINITIONS_REL: Final[str] = "src/synthorg/settings/definitions"

_BASELINE_HEADER: Final[str] = """\
# Frozen baseline of settings that remain restart-bound
# (restart_required=True or read_only_post_init=True), one `<namespace>.<key>`
# per line, sorted. Two categories live here:
#   1. Genuine keeps -- bound OS/transport resources, Litestar middleware,
#      fixed-size buffers, boot-baked logging/audit/TSA, image pins, secrets.
#   2. Deferred -- settings whose conversion is tracked by the sibling issues
#      #2515 (research/knowledge/hr/simulations) and #2516
#      (self_improvement/chief_of_staff); these shrink out as those PRs land.
#
# scripts/check_setting_restart_required_justified.py fails when a restart-bound
# setting is neither on this list nor carries a
# `# lint-allow: restart-required -- <reason>` marker on its register() block.
#
# Regenerate (rare; requires explicit user approval) with:
#   uv run python scripts/check_setting_restart_required_justified.py --update-baseline
"""


@dataclass(frozen=True)
class RestartBoundSetting:
    """A restart-bound setting record extracted from a definitions module."""

    setting_key: str
    source_file: str
    source_line: int
    has_marker: bool


def _line_has_marker(line: str) -> bool:
    """Return True iff *line* carries the restart-required justification marker.

    The marker name must be followed by `` -- `` and non-empty justification
    text (canonical form ``# lint-allow: restart-required -- <reason>``),
    mirroring the project-wide ``# lint-allow:`` convention.
    """
    # Iterate (not ``list(...)``) and swallow a trailing TokenError: an
    # opening ``_r.register(`` line has an unbalanced paren, so tokenize emits
    # the COMMENT and only then raises "EOF in multi-line statement". Building
    # the list eagerly would discard the already-yielded comment and miss a
    # marker sitting on the register opening line.
    try:
        for tok in tokenize.generate_tokens(io.StringIO(line).readline):
            if tok.type != tokenize.COMMENT:
                continue
            comment = tok.string.lstrip("#").strip()
            if not comment.startswith(_MARKER):
                continue
            suffix = comment[len(_MARKER) :].strip()
            if suffix.startswith("--") and suffix[2:].strip():
                return True
    except tokenize.TokenError, IndentationError, SyntaxError:
        return False
    return False


def _resolve_namespace(node: ast.expr | None) -> str | None:
    """Resolve ``SettingNamespace.X`` to its lower-case namespace string."""
    if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
        return None
    if node.value.id != "SettingNamespace":
        return None
    return node.attr.lower()


def _extract_bool(node: ast.expr | None) -> bool:
    """Return the bool-literal value of *node*, or ``False`` when absent/other."""
    return isinstance(node, ast.Constant) and node.value is True


def _enclosing_register(
    call: ast.Call, parents: dict[ast.AST, ast.AST]
) -> ast.Call | None:
    """Return the ``register(...)`` Call enclosing *call*, if any.

    Definitions are declared as ``_r.register(SettingDefinition(...))``; the
    marker sits on that outer call's block, so the window must be the
    enclosing ``register(...)`` span, not the inner ``SettingDefinition(...)``.
    """
    node: ast.AST | None = parents.get(call)
    while node is not None:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "register"
        ):
            return node
        node = parents.get(node)
    return None


def _block_has_marker(
    call: ast.Call, register: ast.Call | None, file_lines: list[str]
) -> bool:
    """True iff the enclosing ``register(...)`` block carries the marker.

    Scans exactly the enclosing ``register(...)`` call span -- from its opening
    ``_r.register(`` line through its closing ``)`` -- so a marker on the
    opening line is seen and a marker on the next adjacent block cannot leak in
    (both failure modes of a fixed line-window keyed off the inner call). Falls
    back to the ``SettingDefinition(...)`` span when no enclosing register is
    found (a definition declared outside the registration helper).
    """
    block: ast.Call = register if register is not None else call
    start = block.lineno - 1
    end = getattr(block, "end_lineno", block.lineno) or block.lineno
    return any(
        _line_has_marker(file_lines[idx])
        for idx in range(start, min(len(file_lines), end))
    )


def _find_definition_calls(tree: ast.Module) -> list[ast.Call]:
    """Return every ``SettingDefinition(...)`` Call node in *tree*."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "SettingDefinition"
    ]


def _scan_file(path: Path, rel: str) -> list[RestartBoundSetting]:
    """Extract the restart-bound settings declared in *path*.

    Raises:
        ValueError: If the file is unreadable or has invalid Python syntax;
            silently dropping a file would let an unjustified flag slip past.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"{rel}: could not read definitions file: {exc}"
        raise ValueError(msg) from exc
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        msg = f"{rel}:{exc.lineno or 0}: syntax error: {exc.msg}"
        raise ValueError(msg) from exc
    file_lines = text.splitlines()
    parents: dict[ast.AST, ast.AST] = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    records: list[RestartBoundSetting] = []
    for call in _find_definition_calls(tree):
        kwargs = {kw.arg: kw.value for kw in call.keywords}
        namespace = _resolve_namespace(kwargs.get("namespace"))
        key_node = kwargs.get("key")
        key = (
            key_node.value
            if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)
            else None
        )
        if namespace is None or key is None:
            continue
        restart_bound = _extract_bool(kwargs.get("restart_required")) or _extract_bool(
            kwargs.get("read_only_post_init")
        )
        if not restart_bound:
            continue
        records.append(
            RestartBoundSetting(
                setting_key=f"{namespace}.{key}",
                source_file=rel,
                source_line=call.lineno,
                has_marker=_block_has_marker(
                    call, _enclosing_register(call, parents), file_lines
                ),
            )
        )
    return records


def scan_definitions(repo_root: Path) -> list[RestartBoundSetting]:
    """Return every restart-bound setting under ``settings/definitions/``."""
    definitions_dir = repo_root / _DEFINITIONS_REL
    records: list[RestartBoundSetting] = []
    for path in sorted(definitions_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        records.extend(_scan_file(path, path.relative_to(repo_root).as_posix()))
    return records


def load_baseline(repo_root: Path) -> set[str]:
    """Load the baseline setting keys, ignoring comments and blanks."""
    path = repo_root / "scripts" / _BASELINE_NAME
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def write_baseline(repo_root: Path, keys: set[str]) -> None:
    """Write *keys* to the baseline file with the documented header."""
    path = repo_root / "scripts" / _BASELINE_NAME
    body = "\n".join(sorted(keys))
    path.write_text(f"{_BASELINE_HEADER}{body}\n", encoding="utf-8")


def evaluate(
    records: list[RestartBoundSetting],
    baseline: set[str],
) -> tuple[list[RestartBoundSetting], set[str]]:
    """Return (unjustified records, stale baseline entries).

    A record is unjustified when it is neither baselined nor marker-justified.
    A baseline entry is stale when no current record carries that key.
    """
    current_keys = {r.setting_key for r in records}
    unjustified = [
        r for r in records if not r.has_marker and r.setting_key not in baseline
    ]
    stale = baseline - current_keys
    return unjustified, stale


def _run(repo_root: Path, *, update_baseline: bool) -> int:
    """Execute the gate; return a process exit code."""
    try:
        records = scan_definitions(repo_root)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    if update_baseline:
        keys = {r.setting_key for r in records if not r.has_marker}
        write_baseline(repo_root, keys)
        sys.stdout.write(
            f"Wrote {len(keys)} restart-bound settings to {_BASELINE_NAME}\n"
        )
        return 0

    baseline = load_baseline(repo_root)
    unjustified, stale = evaluate(records, baseline)

    for key in sorted(stale):
        sys.stdout.write(
            f"WARNING: stale baseline entry {key!r} (converted/removed; "
            f"prune it from {_BASELINE_NAME})\n"
        )

    if unjustified:
        sys.stderr.write(
            "Unjustified restart-bound settings (add a"
            " `# lint-allow: restart-required -- <reason>` marker or, for a"
            f" genuine keep, add to scripts/{_BASELINE_NAME}):\n"
        )
        for record in sorted(unjustified, key=lambda r: r.setting_key):
            sys.stderr.write(
                f"  {record.setting_key} ({record.source_file}:{record.source_line})\n"
            )
        return 1
    sys.stdout.write(
        f"OK: {len(records)} restart-bound settings, all justified"
        f" ({len(baseline)} baselined).\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        Process exit code (0 pass, 1 fail).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args(argv)
    repo_root = args.repo_root or Path(__file__).resolve().parent.parent
    return _run(repo_root, update_baseline=args.update_baseline)


if __name__ == "__main__":
    raise SystemExit(main())
