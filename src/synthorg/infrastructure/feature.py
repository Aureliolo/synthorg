# module-kind: feature
"""Facades feature manifest.

Declares the read / MCP facade family: the :class:`FacadesStateSlice`
that owns the dashboard / MCP read facades aggregating domain services,
and the infrastructure MCP domain (health, settings, providers, backup,
audit-events, users, projects, requests, setup, simulations,
template-packs, integration-health) whose handlers shim through those
facades. The facade family has no dedicated settings namespace and no
REST controllers; the domain controllers live in their own features.
"""

from collections.abc import Mapping

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.infrastructure._construction import wire_construction
from synthorg.infrastructure.state import FacadesStateSlice
from synthorg.meta.mcp.domains.infrastructure import INFRASTRUCTURE_TOOLS
from synthorg.meta.mcp.feature_descriptors import mcp_descriptor


def _infrastructure_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the infrastructure MCP handler map.

    Returns:
        The infrastructure ``{tool_name: ToolHandler}`` map.
    """
    from synthorg.meta.mcp.handlers.infrastructure import (  # noqa: PLC0415
        INFRASTRUCTURE_HANDLERS,
    )

    return INFRASTRUCTURE_HANDLERS


FEATURE: FeatureModule = FeatureManifest(
    name="facades",
    settings_namespace=None,
    state_slice=FacadesStateSlice,
    construction_wirer=wire_construction,
    controllers=(),
    mcp_handlers=(
        mcp_descriptor(
            domain="infrastructure",
            tool_defs=INFRASTRUCTURE_TOOLS,
            handlers=_infrastructure_mcp_handlers,
        ),
    ),
    lifecycle_hooks=(),
    ghost_wired_symbols=(
        "QualityFacadeService",
        "ReviewFacadeService",
    ),
    depends_on=(),
)
