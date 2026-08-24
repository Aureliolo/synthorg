# module-kind: feature
"""Api-core feature manifest.

Declares the cross-cutting API-core surface: the ``api`` settings
namespace, the :class:`ApiCoreStateSlice` that owns services belonging
to no single domain feature (the opaque-pagination cursor secret
today), the cross-cutting REST controllers (health probes, capabilities,
auth, users, setup), and the realtime websocket
handler. The composition root mounts these from this manifest.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api._construction import wire_construction
from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.auth.controllers.api_keys import AuthApiKeysController
from synthorg.api.auth.controllers.bootstrap import AuthBootstrapController
from synthorg.api.auth.controllers.credentials import AuthCredentialsController
from synthorg.api.auth.controllers.identity import AuthIdentityController
from synthorg.api.auth.controllers.session import AuthSessionController
from synthorg.api.auth.controllers.sessions_mgmt import AuthSessionsController
from synthorg.api.controllers.capabilities import CapabilitiesController
from synthorg.api.controllers.health import (
    HealthController,
    LivenessController,
    ReadinessController,
)
from synthorg.api.controllers.setup.agents import SetupAgentsController
from synthorg.api.controllers.setup.company import SetupCompanyController
from synthorg.api.controllers.setup.completion import SetupCompletionController
from synthorg.api.controllers.setup.locales import SetupLocalesController
from synthorg.api.controllers.setup.status import SetupStatusController
from synthorg.api.controllers.users.account import UserController
from synthorg.api.controllers.users.org_roles import UserOrgRolesController
from synthorg.api.controllers.ws import ws_handler
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="api_core",
    settings_namespace=SettingNamespace.API,
    state_slice=ApiCoreStateSlice,
    controllers=(
        LivenessController,
        ReadinessController,
        HealthController,
        CapabilitiesController,
        AuthBootstrapController,
        AuthSessionController,
        AuthCredentialsController,
        AuthIdentityController,
        AuthSessionsController,
        AuthApiKeysController,
        UserController,
        UserOrgRolesController,
        SetupStatusController,
        SetupCompanyController,
        SetupAgentsController,
        SetupLocalesController,
        SetupCompletionController,
    ),
    websocket_handlers=(ws_handler,),
    mcp_handlers=(),
    lifecycle_hooks=(),
    construction_wirer=wire_construction,
    ghost_wired_symbols=(
        "build_chief_of_staff_proposer",
        "build_role_router",
        "build_group_chat_service",
        "GroupInviteCoordinator",
        "build_conversational_actor",
        "build_operator_console",
        "build_chief_of_staff_narrator",
        # The initiative tail. Claimed here rather than by the engine manifest
        # because this is the feature that constructs it: the stages live in
        # engine/initiative/, but nothing reaches them until
        # lifecycle_helpers/initiative_tail_wiring builds them at startup.
        "IntegrationStageService",
        "EvaluationStageService",
        "ReplanTriggerService",
        "InitiativeEvaluator",
        "StageRunner",
    ),
    depends_on=(),
)
