"""Gate: no alphabetical / first-registered provider auto-pick.

Every LLM dispatch must resolve an explicit ``(provider, model)`` pair. A
provider is chosen either from a bound ``{provider, model_id}`` reference or
from the explicit ``providers.default_provider`` (via
``ProviderRegistry.default_provider`` / ``default_provider_resolved_name``);
it is NEVER auto-picked as "whichever provider sorts first". This gate
AST-scans ``src/synthorg/`` and fails on a reintroduction of any of:

1. ``<registry>.list_providers()[0]`` -- indexing the sorted provider list.
2. ``<name>[0]`` where ``<name>`` was assigned from a ``.list_providers()``
   call in the same function (the ``names = registry.list_providers()`` /
   ``names[0]`` idiom).
3. Any reference to the removed ``resolve_for_model`` method (the bare-model
   auto-resolver that picked the alphabetically-first serving provider).

Opt a genuine exception out with a trailing
``# lint-allow: provider-auto-pick -- <reason>`` on the offending line (e.g.
a non-dispatch tier hint at empty-company boot). Pre-existing out-of-scope
offenders live in ``scripts/provider_auto_pick_baseline.txt``; the gate fails
only on a NEW violation.

Usage:
    uv run python scripts/check_no_provider_auto_pick.py
    uv run python scripts/check_no_provider_auto_pick.py --update-baseline

Exit codes:
    0 -- no new violations.
    1 -- a new provider auto-pick was found.
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
_BASELINE_REL: Final[str] = "scripts/provider_auto_pick_baseline.txt"
_MARKER: Final[str] = "lint-allow: provider-auto-pick"
_LIST_PROVIDERS: Final[str] = "list_providers"
_REMOVED_RESOLVER: Final[str] = "resolve_for_model"


def _is_list_providers_call(node: ast.expr) -> bool:
    """Whether *node* is a ``<expr>.list_providers()`` call."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == _LIST_PROVIDERS
    )


def _is_zero_index(node: ast.Subscript) -> bool:
    """Whether *node* subscripts with the literal ``0``."""
    return isinstance(node.slice, ast.Constant) and node.slice.value == 0


def _provider_list_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Names bound to a ``.list_providers()`` result within *func*.

    Covers ``names = registry.list_providers()`` and its ``list(...)`` /
    ``tuple(...)`` / ``sorted(...)`` wrappers, so the ``names[0]`` idiom is
    caught regardless of the wrapper.
    """
    bound: set[str] = set()
    for node in ast.walk(func):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value: ast.expr = node.value
        # Unwrap a single-arg list()/tuple()/sorted() wrapper.
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in {"list", "tuple", "sorted"}
            and value.args
        ):
            value = value.args[0]
        if _is_list_providers_call(value):
            bound.add(target.id)
    return bound


def _scan_module(tree: ast.Module, lines: list[str], relpath: str) -> list[str]:
    """Return every provider-auto-pick finding in one module."""
    findings: list[str] = []

    def _allowed(lineno: int) -> bool:
        return 1 <= lineno <= len(lines) and _MARKER in lines[lineno - 1]

    # 1 + 3: direct list_providers()[0] and any resolve_for_model reference.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Subscript)
            and _is_zero_index(node)
            and _is_list_providers_call(node.value)
            and not _allowed(node.lineno)
        ):
            findings.append(f"{relpath}:{node.lineno}:list_providers()[0]")
        if (
            isinstance(node, ast.Attribute)
            and node.attr == _REMOVED_RESOLVER
            and not _allowed(node.lineno)
        ):
            findings.append(f"{relpath}:{node.lineno}:{_REMOVED_RESOLVER}")

    # 2: name[0] where name came from a list_providers() result in the same func.
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        provider_names = _provider_list_names(node)
        if not provider_names:
            continue
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Subscript)
                and _is_zero_index(sub)
                and isinstance(sub.value, ast.Name)
                and sub.value.id in provider_names
                and not _allowed(sub.lineno)
            ):
                findings.append(  # noqa: PERF401 -- guarded, narrowed append
                    f"{relpath}:{sub.lineno}:{sub.value.id}[0]"
                )
    return findings


def _scan(root: Path) -> list[str]:
    """Return every current violation identifier under *root*.

    Raises:
        GateSourceError: When the source tree is missing, so a misconfigured
            ``--repo-root`` fails closed rather than scanning zero files.
    """
    src_dir = root / _SRC_REL
    if not src_dir.is_dir():
        msg = f"expected source tree not found: {src_dir}"
        raise GateSourceError(msg)
    findings: list[str] = []
    for path in sorted(src_dir.rglob("*.py")):
        relpath = path.relative_to(root).as_posix()
        text, tree = read_and_parse(path)
        findings.extend(_scan_module(tree, text.splitlines(), relpath))
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
    """Scan for provider auto-picks and return the gate exit code."""
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
            "# Pre-existing provider auto-picks, out of scope for the\n"
            "# no-provider-auto-pick policy. The gate fails on a NEW entry.\n"
        )
        baseline_path.write_text(
            header + body + ("\n" if body else ""), encoding="utf-8"
        )
        print(f"wrote {len(findings)} entries to {_BASELINE_REL}")
        return 0

    new = sorted(findings - _read_baseline(baseline_path))
    if new:
        print(
            "error: provider auto-pick(s) found (resolve an explicit provider "
            "via a bound ref or providers.default_provider, never the first "
            "registered):",
            file=sys.stderr,
        )
        for ident in new:
            print(f"  {ident}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
