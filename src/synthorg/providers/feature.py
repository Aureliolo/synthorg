# module-kind: feature
"""Providers feature manifest.

Declares the providers feature's surface: its ``providers`` settings
namespace, the :class:`ProvidersStateSlice` (registry, router, health
tracker, management / audit / preset-override services), and the
per-sub-domain provider REST controllers (CRUD, connection, model
catalogue, local-model lifecycle, presets, capabilities, allowlists,
audit). The composition root mounts these from this manifest.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.providers.allowlists import ProviderAllowlistsController
from synthorg.api.controllers.providers.audit import ProviderAuditController
from synthorg.api.controllers.providers.capabilities import (
    ProviderCapabilitiesController,
)
from synthorg.api.controllers.providers.connection import ProviderConnectionController
from synthorg.api.controllers.providers.crud import ProviderCrudController
from synthorg.api.controllers.providers.local_models import (
    ProviderLocalModelsController,
)
from synthorg.api.controllers.providers.models import ProviderModelsController
from synthorg.api.controllers.providers.presets import ProviderPresetsController
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="providers",
    settings_namespace=SettingNamespace.PROVIDERS,
    state_slice=ProvidersStateSlice,
    controllers=(
        ProviderCrudController,
        ProviderConnectionController,
        ProviderModelsController,
        ProviderLocalModelsController,
        ProviderPresetsController,
        ProviderCapabilitiesController,
        ProviderAllowlistsController,
        ProviderAuditController,
    ),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
