"""Factory and config for the pluggable structure-map scanner set.

``enabled_ecosystems`` is the config discriminator selecting which
ecosystem-specific scanners are active; the generic fallback is always
appended (the aggregator invokes it only when no specific scanner matched).
"""

from collections.abc import Callable
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.codebase_structure_map import Ecosystem
from synthorg.engine.brownfield.scanner.generic_scanner import GenericScanner
from synthorg.engine.brownfield.scanner.go_scanner import GoScanner
from synthorg.engine.brownfield.scanner.node_scanner import NodeScanner
from synthorg.engine.brownfield.scanner.protocol import (
    StructureMapScanner,
)
from synthorg.engine.brownfield.scanner.python_scanner import PythonScanner
from synthorg.engine.brownfield.scanner.rust_scanner import RustScanner

_DEFAULT_ECOSYSTEMS: Final[tuple[Ecosystem, ...]] = (
    Ecosystem.PYTHON,
    Ecosystem.JAVASCRIPT,
    Ecosystem.GO,
    Ecosystem.RUST,
)

_BUILDERS: Final[dict[Ecosystem, Callable[[], StructureMapScanner]]] = {
    Ecosystem.PYTHON: PythonScanner,
    Ecosystem.JAVASCRIPT: NodeScanner,
    Ecosystem.GO: GoScanner,
    Ecosystem.RUST: RustScanner,
}


class BrownfieldScanConfig(BaseModel):
    """Operator-tunable structure-map scanner configuration.

    Default-constructed enables every shipped ecosystem scanner. The
    generic fallback is always present regardless of this setting.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled_ecosystems: tuple[Ecosystem, ...] = Field(
        default=_DEFAULT_ECOSYSTEMS,
        description="Ecosystem-specific scanners to activate",
    )


def build_structure_map_scanners(
    config: BrownfieldScanConfig | None = None,
) -> tuple[StructureMapScanner, ...]:
    """Build the active scanner set (generic fallback always appended).

    Args:
        config: Scanner configuration; defaults to all ecosystems enabled.

    Returns:
        Ecosystem-specific scanners (in canonical order) followed by the
        generic fallback scanner.
    """
    resolved = config if config is not None else BrownfieldScanConfig()
    enabled = resolved.enabled_ecosystems
    specific: list[StructureMapScanner] = [
        _BUILDERS[eco]() for eco in _DEFAULT_ECOSYSTEMS if eco in enabled
    ]
    specific.append(GenericScanner())
    return tuple(specific)
