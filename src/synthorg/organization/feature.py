# module-kind: feature
"""Organization feature manifest.

Declares the organization feature's surface: the
:class:`OrganizationStateSlice` (company / department / role / team
services). The organization domain has no dedicated settings
namespace. Controllers stay hand-wired in ``api/app.py``; this
manifest is declarative and feeds the navigation index.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.organization.state import OrganizationStateSlice

FEATURE: FeatureModule = FeatureManifest(
    name="organization",
    settings_namespace=None,
    state_slice=OrganizationStateSlice,
    controllers=(),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
