# module-kind: feature
"""Organization feature manifest.

Declares the organization feature's surface: the
:class:`OrganizationStateSlice` (company / department / role / team
services) and the company / department / team / template-pack /
version REST controllers mounted by the composition root. The
organization domain has no dedicated settings namespace.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.company import CompanyController
from synthorg.api.controllers.company_versions import CompanyVersionController
from synthorg.api.controllers.departments import DepartmentController
from synthorg.api.controllers.role_versions import RoleVersionController
from synthorg.api.controllers.teams import TeamController
from synthorg.api.controllers.template_packs import TemplatePackController
from synthorg.organization.state import OrganizationStateSlice

FEATURE: FeatureModule = FeatureManifest(
    name="organization",
    settings_namespace=None,
    state_slice=OrganizationStateSlice,
    controllers=(
        CompanyController,
        CompanyVersionController,
        DepartmentController,
        TeamController,
        RoleVersionController,
        TemplatePackController,
    ),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
