"""Pluggable, deterministic structure-map scanners.

Each :class:`StructureMapScanner` recognises one ecosystem (Python, Node,
Go, Rust) and contributes the modules / entry points / tests / build files
/ dependencies it can read from that ecosystem's manifests. A generic
file-tree scanner is the always-present safe default. The aggregator runs
every scanner whose ``detect`` matches and merges their contributions into
one :class:`~synthorg.core.codebase_structure_map.CodebaseStructureMap`.
"""

from synthorg.engine.brownfield.scanner.aggregator import scan_codebase
from synthorg.engine.brownfield.scanner.factory import (
    BrownfieldScanConfig,
    build_structure_map_scanners,
)
from synthorg.engine.brownfield.scanner.protocol import (
    EcosystemScan,
    StructureMapScanner,
)

__all__ = [
    "BrownfieldScanConfig",
    "EcosystemScan",
    "StructureMapScanner",
    "build_structure_map_scanners",
    "scan_codebase",
]
