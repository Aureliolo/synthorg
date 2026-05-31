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
from synthorg.api._construction import wire_construction
from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.auth.controllers.bootstrap import AuthBootstrapController
from synthorg.api.auth.controllers.credentials import AuthCredentialsController
from synthorg.api.auth.controllers.identity import AuthIdentityController
from synthorg.api.auth.controllers.session import AuthSessionController
from synthorg.api.auth.controllers.sessions_mgmt import AuthSessionsController
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
        AuthBootstrapController,
        AuthSessionController,
        AuthCredentialsController,
        AuthIdentityController,
        AuthSessionsController,
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
    construction_wirer=wire_construction,
    ghost_wired_symbols=(
        "build_chief_of_staff_proposer",
        "build_role_router",
        "build_group_chat_service",
    ),
    depends_on=(),
)
