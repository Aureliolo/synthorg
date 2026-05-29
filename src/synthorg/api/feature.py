# module-kind: feature
"""Api-core feature manifest.

Declares the cross-cutting API-core surface: the ``api`` settings
namespace, the :class:`ApiCoreStateSlice` that owns services belonging
to no single domain feature (the opaque-pagination cursor secret
today), the cross-cutting REST controllers (health probes, capabilities,
auth, users, setup, setup-personality), and the realtime websocket
handler. The composition root mounts these from this manifest.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.auth.controller import AuthController
from synthorg.api.controllers.capabilities import CapabilitiesController
from synthorg.api.controllers.health import (
    LivenessController,
    ReadinessController,
)
from synthorg.api.controllers.setup.agents import SetupAgentsController
from synthorg.api.controllers.setup.company import SetupCompanyController
from synthorg.api.controllers.setup.completion import SetupCompletionController
from synthorg.api.controllers.setup.locales import SetupLocalesController
from synthorg.api.controllers.setup.status import SetupStatusController
from synthorg.api.controllers.setup_personality import SetupPersonalityController
from synthorg.api.controllers.users import UserController
from synthorg.api.controllers.ws import ws_handler
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="api_core",
    settings_namespace=SettingNamespace.API,
    state_slice=ApiCoreStateSlice,
    controllers=(
        LivenessController,
        ReadinessController,
        CapabilitiesController,
        AuthController,
        UserController,
        SetupStatusController,
        SetupCompanyController,
        SetupAgentsController,
        SetupLocalesController,
        SetupCompletionController,
        SetupPersonalityController,
    ),
    websocket_handlers=(ws_handler,),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=("build_chief_of_staff_proposer",),
    depends_on=(),
)
