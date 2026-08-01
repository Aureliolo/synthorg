# module-kind: feature
"""Subsystem-reconciliation feature manifest.

Declares the slice holding the reconciler and the read-only controller that
reports each subsystem's phase. The feature has no settings namespace of its
own: the keys it reacts to belong to the subsystems being reconciled, and
each declares them on its own ``SubsystemSpec``.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.subsystems import SubsystemsController
from synthorg.api.subsystems.state import SubsystemsStateSlice

FEATURE: FeatureModule = FeatureManifest(
    name="subsystems",
    settings_namespace=None,
    state_slice=SubsystemsStateSlice,
    controllers=(SubsystemsController,),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(
        "SubsystemReconciler",
        "SubsystemResyncScheduler",
    ),
    depends_on=(),
)
