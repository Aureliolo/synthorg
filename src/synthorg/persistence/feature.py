# module-kind: feature
"""Persistence feature manifest.

Declares the persistence feature's surface: the
:class:`PersistenceStateSlice` holding the connected backend. The
persistence layer has no dedicated settings namespace. Wiring stays
hand-coded in ``api`` startup; this manifest is declarative and feeds
the navigation index.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.persistence.state import PersistenceStateSlice

FEATURE: FeatureModule = FeatureManifest(
    name="persistence",
    settings_namespace=None,
    state_slice=PersistenceStateSlice,
    controllers=(),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
