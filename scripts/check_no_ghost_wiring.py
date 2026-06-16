#!/usr/bin/env python3
"""Pre-push / CI gate: no ghost-wiring regressions.

A "ghost" is a runtime component that is defined and unit-tested but never
constructed/called in the shipped ``src/`` boot path, so it can never run in
a real deployment. Issue #1951 / EPIC #1955 found a whole runtime
(``AgentEngine``, the coordinator, coordination metrics, the intake engine)
in this state. It hid because nothing enforced "if it ships, it must be
reachable".

This gate is manifest-driven (``scripts/_ghost_wiring_manifest.txt``). For
every ``ENFORCED`` symbol it asserts there is at least one
construction/call site in ``src/synthorg/`` *outside* the symbol's own
defining module and *outside* ``tests/``. Zero such sites means the symbol
has regressed into a ghost; the gate fails.

``PENDING`` symbols (known ghosts still being wired by an EPIC #1955 issue)
are not enforced yet -- enforcing now would fail until the wiring PR lands.
Per the Convention Rollout rule the wiring PR flips its line
``PENDING -> ENFORCED`` in the same change. The gate prints an advisory
nudge when a ``PENDING`` symbol already looks constructed so reviewers
remember to promote it.

Scope rule (avoids a false-positive flood): only runtime modules expected
to be wired at boot are considered construction sites
(``engine/``, ``workers/``, ``api/``, ``budget/``, ``security/``,
``meta/``, ``client/``, plus the ``settings`` resolver path). Public
library / Pydantic / API-schema types meant for external instantiation are
out of scope by construction -- they simply are not put in the manifest.

AST-based. Fail-closed on a syntax error in a scanned file.

Usage::

    uv run python scripts/check_no_ghost_wiring.py
    uv run python scripts/check_no_ghost_wiring.py --repo-root /path/to/repo
"""

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable

MANIFEST = Path("scripts/_ghost_wiring_manifest.txt")

_State = Literal["ENFORCED", "PENDING"]
_VALID_STATES: Final[tuple[_State, ...]] = ("ENFORCED", "PENDING")

# "<STATE> <symbol> <issue>" -- the exact head fields before " -- ".
_MANIFEST_HEAD_FIELDS: Final[int] = 3

# The mandatory delimiter between the manifest head and its note.
_MANIFEST_DELIM: Final[str] = " -- "

# Cap on construction sites listed in a PENDING-symbol advisory nudge.
_MAX_NUDGE_SITES: Final[int] = 3

# Runtime areas where a symbol being constructed means "wired at boot".
# A construction site anywhere under these prefixes counts as reachable.
RUNTIME_PREFIXES: Final[tuple[str, ...]] = (
    "src/synthorg/engine/",
    "src/synthorg/workers/",
    "src/synthorg/api/",
    "src/synthorg/budget/",
    "src/synthorg/security/",
    "src/synthorg/meta/",
    # infrastructure/ holds the read / MCP facade family; its services are
    # constructed at boot by the facades feature construction_wirer
    # (infrastructure/_construction.py) via run_construction_wiring.
    "src/synthorg/infrastructure/",
    "src/synthorg/client/",
    "src/synthorg/settings/",
    # tools/ is reached at boot via the
    # ``build_default_tools_from_config`` chain called from
    # ``workers/runtime_builder._build_tool_registry``; counting tool
    # factory + tool-class construction lets the manifest track tool
    # wiring (e.g. EPIC #1987 children that add new tool classes).
    "src/synthorg/tools/",
    "src/synthorg/docs_engine/",
    # knowledge/ is reached at boot via api/app.py::_wire_knowledge_engine
    # (build_knowledge_service / build_knowledge_tool_factory); counting its
    # factory + tool-factory construction lets the manifest track the
    # knowledge substrate's wiring (#1988).
    "src/synthorg/knowledge/",
    # project_brain/ is reached at boot via
    # api/lifecycle_helpers/feature_wiring.py::_wire_project_brain
    # (build_project_brain_service); counting its factory + tool-factory
    # construction lets the manifest track the project-brain wiring (#1996).
    "src/synthorg/project_brain/",
    # research/ is reached at boot via api/app.py::_wire_research_engine
    # (build_research_service / build_research_tool_factory); counting its
    # factory + strategy + tool-factory construction lets the manifest track
    # the research subsystem's wiring (#1989).
    "src/synthorg/research/",
    # deliverable_receipts/ is reached at boot via
    # api/lifecycle_helpers/feature_wiring.py::_wire_deliverable_receipts
    # (build_deliverable_receipt_service); counting its factory construction
    # lets the manifest track the provenance-receipt wiring (#1999).
    "src/synthorg/deliverable_receipts/",
)


@dataclass(frozen=True)
class ManifestEntry:
    """One symbol the manifest tracks."""

    state: _State
    symbol: str
    issue: str
    note: str
    lineno: int


def _parse_manifest(path: Path) -> list[ManifestEntry]:
    entries: list[ManifestEntry] = []
    for idx, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, sep, note = line.partition(_MANIFEST_DELIM)
        parts = head.split()
        if (
            sep != _MANIFEST_DELIM
            or len(parts) != _MANIFEST_HEAD_FIELDS
            or parts[0] not in _VALID_STATES
        ):
            msg = (
                f"{path}:{idx}: malformed manifest line "
                f"(expect '<STATE> <symbol> <issue> -- <note>'): {raw!r}"
            )
            raise ValueError(msg)
        entries.append(
            ManifestEntry(
                state=parts[0],
                symbol=parts[1],
                issue=parts[2],
                note=note.strip(),
                lineno=idx,
            )
        )
    return entries


def _iter_runtime_py(repo_root: Path) -> Iterable[Path]:
    paths: set[Path] = set()
    for prefix in RUNTIME_PREFIXES:
        base = repo_root / prefix
        if base.is_dir():
            paths.update(base.rglob("*.py"))
    yield from sorted(paths)


@dataclass(frozen=True)
class _Sites:
    """Per-symbol call sites and definition sites (repo-relative posix)."""

    calls: dict[str, set[str]]
    defs: dict[str, set[str]]


_DefOrCall = ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef


def _index_tree(tree: ast.AST, symbols: set[str]) -> tuple[set[str], set[str]]:
    """Return ``(defined, called)`` tracked symbol names in one module.

    Bare-name attribute matching (``x.Symbol(...)``) is a deliberate
    heuristic: the manifest tracks distinctive runtime class/factory
    names, so cross-object name collisions are negligible in practice.
    """
    defined: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, _DefOrCall):
            if node.name in symbols:
                defined.add(node.name)
            continue
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            name: str | None = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            name = None
        if name in symbols:
            called.add(name)
    return defined, called


def _scan_sites(repo_root: Path, symbols: set[str]) -> _Sites:
    """Map each symbol to runtime files that call it and that define it.

    A *call site* is an ``ast.Call`` whose callee is the bare symbol name
    (``Symbol(...)``) or an attribute ending in it (``mod.Symbol(...)``).
    A *definition site* is the file holding the symbol's own
    ``class``/``def``. ``_run`` treats a symbol as constructed only when
    it has a call site *outside* its own defining module, matching the
    manifest contract ("outside its own defining module and tests/").

    Fail-closed: an unreadable, non-UTF8, or unparsable source file
    raises ``OSError``/``SyntaxError`` with file context rather than
    silently under-reporting construction sites.
    """
    calls: dict[str, set[str]] = {s: set() for s in symbols}
    defs: dict[str, set[str]] = {s: set() for s in symbols}
    for py in _iter_runtime_py(repo_root):
        rel = py.relative_to(repo_root).as_posix()
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (OSError, UnicodeDecodeError) as exc:
            msg = f"{py}: {exc}"
            raise OSError(msg) from exc
        except SyntaxError as exc:
            msg = f"{py}: {exc}"
            raise SyntaxError(msg) from exc
        defined, called = _index_tree(tree, symbols)
        for sym in defined:
            defs[sym].add(rel)
        for sym in called:
            calls[sym].add(rel)
    return _Sites(calls=calls, defs=defs)


def _claimed_symbols_from_features(repo_root: Path) -> frozenset[str]:
    """Aggregate ``ghost_wired_symbols`` from every ``feature.py`` under *repo_root*.

    Pure AST walk: parses each ``src/synthorg/**/feature.py``, finds the
    ``FEATURE = FeatureManifest(... ghost_wired_symbols=(...))`` kwarg, and
    collects the string literals. Avoids importing the synthorg package
    (the gate runs at pre-push; an AST scan is faster and tolerates the
    boot-time import cycle the live ``discover_features`` would trip).
    """
    src_root = repo_root / "src" / "synthorg"
    if not src_root.is_dir():
        return frozenset()
    symbols: set[str] = set()
    for feature_py in sorted(src_root.rglob("feature.py")):
        try:
            tree = ast.parse(
                feature_py.read_text(encoding="utf-8"), filename=str(feature_py)
            )
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            # A feature.py that does not parse silently dropping its claims
            # would mask real ghost-wiring violations: a symbol legitimately
            # claimed there would look orphan to this gate. Re-raise with the
            # offending file in the message so the operator can fix the root
            # cause; check_feature_manifest reports the same file in its own
            # findings, but failing fast here keeps the parity diagnosis honest.
            msg = f"{feature_py}: {exc}"
            raise OSError(msg) from exc
        for value in _ghost_wired_kwarg_values(tree):
            symbols.update(value)
    return frozenset(symbols)


def _ghost_wired_kwarg_values(tree: ast.AST) -> list[list[str]]:
    """Return ``ghost_wired_symbols`` from the module-level ``FEATURE`` manifest.

    Only inspects ``FEATURE = FeatureManifest(...)`` (or annotated form) at the
    module top level: walking every call would let an unrelated helper call
    inside ``feature.py`` smuggle symbols into the parity check and silently
    bypass it.
    """
    if not isinstance(tree, ast.Module):
        return []
    values: list[list[str]] = []
    for node in tree.body:
        targets: list[ast.expr]
        value: ast.expr | None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if value is None or not _targets_module_feature(targets):
            continue
        if not isinstance(value, ast.Call) or not _is_feature_manifest_call(value):
            continue
        for keyword in value.keywords:
            if keyword.arg != "ghost_wired_symbols":
                continue
            arg = keyword.value
            if isinstance(arg, (ast.Tuple, ast.List)):
                values.append(
                    [
                        element.value
                        for element in arg.elts
                        if isinstance(element, ast.Constant)
                        and isinstance(element.value, str)
                    ]
                )
    return values


def _targets_module_feature(targets: list[ast.expr]) -> bool:
    """Return ``True`` when *targets* assign the module-level ``FEATURE`` name."""
    for target in targets:
        if isinstance(target, ast.Name) and target.id == "FEATURE":
            return True
    return False


def _is_feature_manifest_call(call: ast.Call) -> bool:
    """Return ``True`` when *call* invokes ``FeatureManifest`` (bare or attr)."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == "FeatureManifest"
    if isinstance(func, ast.Attribute):
        return func.attr == "FeatureManifest"
    return False


def _check_parity(
    entries: list[ManifestEntry],
    claimed: frozenset[str],
) -> list[str]:
    """Return human-readable parity failures between manifest and claims."""
    enforced = frozenset(e.symbol for e in entries if e.state == "ENFORCED")
    manifest_only = enforced - claimed
    feature_only = claimed - enforced
    lines: list[str] = []
    if manifest_only:
        lines.append(
            "ghost-wiring parity: ENFORCED symbols missing from every "
            "feature.py ghost_wired_symbols claim:"
        )
        lines.extend(f"  - {sym}" for sym in sorted(manifest_only))
    if feature_only:
        lines.append(
            "ghost-wiring parity: symbols claimed by a feature.py manifest "
            "but missing from scripts/_ghost_wiring_manifest.txt:"
        )
        lines.extend(f"  - {sym}" for sym in sorted(feature_only))
    return lines


def _run(
    repo_root: Path,
    *,
    claimed_symbols: frozenset[str] | None = None,
) -> int:
    repo_root = repo_root.resolve()
    manifest_path = repo_root / MANIFEST
    if not manifest_path.is_file():
        print(f"ghost-wiring gate: manifest missing at {MANIFEST}")
        return 1
    try:
        entries = _parse_manifest(manifest_path)
    except (OSError, ValueError) as exc:
        print(f"ghost-wiring gate: cannot read manifest ({MANIFEST}): {exc}")
        return 1
    symbols = {e.symbol for e in entries}
    try:
        sites = _scan_sites(repo_root, symbols)
    except (OSError, SyntaxError) as exc:
        print(f"ghost-wiring gate: cannot scan src/ (fail-closed): {exc}")
        return 1

    external = {e.symbol: sites.calls[e.symbol] - sites.defs[e.symbol] for e in entries}
    failures: list[ManifestEntry] = []
    nudges: list[ManifestEntry] = []
    for e in entries:
        if e.state == "ENFORCED" and not external[e.symbol]:
            failures.append(e)
        elif e.state == "PENDING" and external[e.symbol]:
            nudges.append(e)

    for e in nudges:
        where = ", ".join(sorted(external[e.symbol])[:_MAX_NUDGE_SITES])
        print(
            f"ghost-wiring NUDGE: PENDING {e.symbol} ({e.issue}) now has "
            f"construction sites ({where}). Its wiring PR should flip it to "
            f"ENFORCED in scripts/_ghost_wiring_manifest.txt:{e.lineno}."
        )

    if claimed_symbols is not None:
        parity_lines = _check_parity(entries, claimed_symbols)
        if parity_lines:
            for line in parity_lines:
                print(line)
            return 1

    if not failures:
        return 0

    print("Ghost-wiring regression -- ENFORCED runtime components with no")
    print("construction/call site in the shipped src/ boot path:")
    for e in failures:
        print(f"  {e.symbol} ({e.issue}) -- {e.note}")
        print(f"    manifest: scripts/_ghost_wiring_manifest.txt:{e.lineno}")
    print(
        "\nFix: restore the boot-path construction (see the issue), or, if "
        "the component was deliberately removed, delete its manifest line "
        "in the same PR. An ENFORCED symbol that ships unreachable is the "
        "exact #1951 / EPIC #1955 defect this gate exists to prevent."
    )
    return 1


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    return _run(
        repo_root,
        claimed_symbols=_claimed_symbols_from_features(repo_root),
    )


if __name__ == "__main__":
    sys.exit(main())
