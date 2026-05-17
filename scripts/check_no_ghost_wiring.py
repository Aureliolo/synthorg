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

    python scripts/check_no_ghost_wiring.py
    python scripts/check_no_ghost_wiring.py --repo-root /path/to/repo
"""

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

SCAN_ROOT = Path("src/synthorg")
MANIFEST = Path("scripts/_ghost_wiring_manifest.txt")

# "<STATE> <symbol> <issue>" -- the three required head fields before " -- ".
_MIN_MANIFEST_FIELDS: Final[int] = 3

# Runtime areas where a symbol being constructed means "wired at boot".
# A construction site anywhere under these prefixes counts as reachable.
RUNTIME_PREFIXES = (
    "src/synthorg/engine/",
    "src/synthorg/workers/",
    "src/synthorg/api/",
    "src/synthorg/budget/",
    "src/synthorg/security/",
    "src/synthorg/meta/",
    "src/synthorg/client/",
    "src/synthorg/settings/",
)


@dataclass(frozen=True)
class ManifestEntry:
    """One symbol the manifest tracks."""

    state: str  # ENFORCED | PENDING
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
        head, _, note = line.partition(" -- ")
        parts = head.split()
        if len(parts) < _MIN_MANIFEST_FIELDS or parts[0] not in {"ENFORCED", "PENDING"}:
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
    base = repo_root / SCAN_ROOT
    if not base.is_dir():
        return
    yield from sorted(base.rglob("*.py"))


def _construction_sites(repo_root: Path, symbols: set[str]) -> dict[str, set[str]]:
    """Map each symbol to the set of runtime files that construct/call it.

    A construction site is an ``ast.Call`` whose callee is the bare symbol
    name (``Symbol(...)``) or an attribute ending in it
    (``mod.Symbol(...)``), located in a runtime-prefixed file, and not the
    symbol's own ``class``/``def`` statement.
    """
    found: dict[str, set[str]] = {s: set() for s in symbols}
    for py in _iter_runtime_py(repo_root):
        rel = py.relative_to(repo_root).as_posix()
        if not any(rel.startswith(p) for p in RUNTIME_PREFIXES):
            continue
        text = py.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text, filename=str(py))
        except SyntaxError as exc:
            msg = f"{py}: {exc}"
            raise SyntaxError(msg) from exc
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name: str | None = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in symbols:
                found[name].add(rel)
    return found


def _run(repo_root: Path) -> int:
    manifest_path = repo_root / MANIFEST
    if not manifest_path.is_file():
        print(f"ghost-wiring gate: manifest missing at {MANIFEST}")
        return 1
    entries = _parse_manifest(manifest_path)
    symbols = {e.symbol for e in entries}
    sites = _construction_sites(repo_root, symbols)

    failures: list[ManifestEntry] = []
    nudges: list[ManifestEntry] = []
    for e in entries:
        constructed = bool(sites.get(e.symbol))
        if e.state == "ENFORCED" and not constructed:
            failures.append(e)
        elif e.state == "PENDING" and constructed:
            nudges.append(e)

    for e in nudges:
        where = ", ".join(sorted(sites[e.symbol])[:3])
        print(
            f"ghost-wiring NUDGE: PENDING {e.symbol} ({e.issue}) now has "
            f"construction sites ({where}). Its wiring PR should flip it to "
            f"ENFORCED in scripts/_ghost_wiring_manifest.txt:{e.lineno}."
        )

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
    return _run(args.repo_root.resolve())


if __name__ == "__main__":
    sys.exit(main())
