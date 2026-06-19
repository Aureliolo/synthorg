# module-kind: feature
"""Observability feature manifest.

Declares the observability feature's surface: its settings namespace,
state slice (Prometheus collector + trace handler), and the metrics
REST controller mounted by the composition root. The collectors are
built directly at boot, so the feature has no MCP domain.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.metrics import MetricsController
from synthorg.observability.state import ObservabilityStateSlice
from synthorg.settings.enums import SettingNamespace

FEATURE: FeatureModule = FeatureManifest(
    name="observability",
    settings_namespace=SettingNamespace.OBSERVABILITY,
    state_slice=ObservabilityStateSlice,
    controllers=(MetricsController,),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=("install_audit_chain_sink",),
    depends_on=(),
)
