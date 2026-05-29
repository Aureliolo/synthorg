# module-kind: feature
"""HR feature manifest.

Declares the HR feature's surface: its ``hr`` settings namespace, the
:class:`HrStateSlice` (agent registry, performance, training,
personalities, versions, activity, health, scaling), and its REST
controllers (agents, identity versions, activity, personalities,
scaling, training, quality, collaboration) mounted by the composition
root.
"""

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
from synthorg.hr.state import HrStateSlice
from synthorg.settings.enums import SettingNamespace

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
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
