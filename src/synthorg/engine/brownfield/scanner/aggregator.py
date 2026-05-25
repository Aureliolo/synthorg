"""Structure-map aggregation: run scanners, merge, hash.

Runs every ecosystem-specific scanner whose ``detect`` matches; when none
match, falls back to the generic scanner. Merges the contributions into a
single :class:`CodebaseStructureMap`, de-duplicating repeated entries and
stamping a content hash over the structural facts (independent of
``project_id`` and ``scanned_at``) so a same-source re-import short-circuits.
"""

import asyncio
from pathlib import Path  # noqa: TC003 -- runtime annotation (PEP 649 introspection)

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.codebase_structure_map import (
    CodebaseStructureMap,
    Ecosystem,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001 -- runtime annotation
from synthorg.engine.brownfield.scanner.protocol import (
    EcosystemScan,
    StructureMapScanner,
)
from synthorg.versioning.hashing import compute_content_hash


def _merge(scans: list[EcosystemScan]) -> EcosystemScan:
    """Merge contributions, de-duplicating while preserving sorted order.

    Returns:
        A single :class:`EcosystemScan` with each collection
        de-duplicated and sorted (by item ``repr``).
    """

    def _dedupe[T](items: list[T]) -> tuple[T, ...]:
        seen: dict[str, T] = {}
        for item in items:
            seen.setdefault(repr(item), item)
        return tuple(seen[key] for key in sorted(seen))

    return EcosystemScan(
        modules=_dedupe([m for s in scans for m in s.modules]),
        entry_points=_dedupe([e for s in scans for e in s.entry_points]),
        test_suites=_dedupe([t for s in scans for t in s.test_suites]),
        build_files=_dedupe([b for s in scans for b in s.build_files]),
        dependencies=_dedupe([d for s in scans for d in s.dependencies]),
    )


def _run_scanners(
    workspace_path: Path,
    scanners: tuple[StructureMapScanner, ...],
) -> EcosystemScan:
    """Run matching specific scanners, else the generic fallback (sync).

    Returns:
        Merged :class:`EcosystemScan` from matching specific
        scanners; the merged generic-scanner output when no specific
        scanner matched; an empty scan when no scanner matched.
    """
    specific = [s for s in scanners if s.ecosystem() is not Ecosystem.GENERIC]
    generic = [s for s in scanners if s.ecosystem() is Ecosystem.GENERIC]
    matched = [s.scan(workspace_path) for s in specific if s.detect(workspace_path)]
    if matched:
        return _merge(matched)
    fallback = [s.scan(workspace_path) for s in generic if s.detect(workspace_path)]
    return _merge(fallback)


async def scan_codebase(
    *,
    workspace_path: Path,
    project_id: NotBlankStr,
    source_ref: NotBlankStr,
    scanners: tuple[StructureMapScanner, ...],
    clock: Clock | None = None,
) -> CodebaseStructureMap:
    """Scan *workspace_path* into a persisted-ready structure map.

    The scan itself is synchronous and CPU/IO-bound, so it runs in a worker
    thread. The returned map's ``content_hash`` covers only the structural
    facts, so re-scanning an unchanged source yields an identical hash.

    Returns:
        A persistence-ready :class:`CodebaseStructureMap` carrying the
        merged scan plus ``project_id``, ``source_ref``, ``scanned_at``
        and a content hash computed over the structural facts only.
    """
    resolved_clock = clock if clock is not None else SystemClock()
    merged = await asyncio.to_thread(_run_scanners, workspace_path, scanners)
    content_hash = compute_content_hash(merged)
    return CodebaseStructureMap(
        project_id=project_id,
        source_ref=source_ref,
        modules=merged.modules,
        entry_points=merged.entry_points,
        test_suites=merged.test_suites,
        build_files=merged.build_files,
        dependencies=merged.dependencies,
        scanned_at=resolved_clock.now(),
        content_hash=content_hash,
    )
