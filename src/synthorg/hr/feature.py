# module-kind: feature
"""HR feature manifest.

Declares the HR feature's surface: its ``hr`` settings namespace, the
:class:`HrStateSlice` (agent registry, performance, training,
personalities, versions, activity, health, scaling), its REST
controllers (agents, identity versions, activity, personalities,
scaling, training, quality, collaboration), and the agents + quality
MCP domains mounted by the composition root.
"""

from collections.abc import Mapping

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.activities import ActivityController
from synthorg.api.controllers.agent_identity_versions import (
    AgentIdentityVersionController,
)
from synthorg.api.controllers.agents import AgentController
from synthorg.api.controllers.collaboration import CollaborationController
from synthorg.api.controllers.personalities import PersonalityPresetController
from synthorg.api.controllers.quality import QualityController
from synthorg.api.controllers.scaling import ScalingController
from synthorg.api.controllers.training import TrainingController
from synthorg.hr._construction import wire_construction
from synthorg.hr.state import HrStateSlice
from synthorg.meta.mcp.domains.agents import AGENT_TOOLS
from synthorg.meta.mcp.domains.quality import QUALITY_TOOLS
from synthorg.meta.mcp.feature_descriptors import mcp_descriptor
from synthorg.settings.enums import SettingNamespace


def _agent_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the agents MCP handler map.

    Returns:
        The agents ``{tool_name: ToolHandler}`` map.
    """
    from synthorg.meta.mcp.handlers.agents import AGENT_HANDLERS  # noqa: PLC0415

    return AGENT_HANDLERS


def _quality_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the quality MCP handler map.

    Returns:
        The quality ``{tool_name: ToolHandler}`` map.
    """
    from synthorg.meta.mcp.handlers.quality import QUALITY_HANDLERS  # noqa: PLC0415

    return QUALITY_HANDLERS


FEATURE: FeatureModule = FeatureManifest(
    name="hr",
    settings_namespace=SettingNamespace.HR,
    state_slice=HrStateSlice,
    controllers=(
        AgentController,
        AgentIdentityVersionController,
        ActivityController,
        PersonalityPresetController,
        ScalingController,
        TrainingController,
        QualityController,
        CollaborationController,
    ),
    mcp_handlers=(
        mcp_descriptor(
            domain="agents",
            tool_defs=AGENT_TOOLS,
            handlers=_agent_mcp_handlers,
        ),
        mcp_descriptor(
            domain="quality",
            tool_defs=QUALITY_TOOLS,
            handlers=_quality_mcp_handlers,
        ),
    ),
    lifecycle_hooks=(),
    construction_wirer=wire_construction,
    ghost_wired_symbols=(),
    depends_on=(),
)
