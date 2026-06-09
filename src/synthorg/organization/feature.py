# module-kind: feature
"""Organization feature manifest.

Declares the organization feature's surface: the
:class:`OrganizationStateSlice` (company / department / role / team
services), the company / department / team / template-pack / version
REST controllers, and the organization MCP domain mounted by the
composition root. The organization domain has no dedicated settings
namespace.
"""

from collections.abc import Mapping

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.company import CompanyController
from synthorg.api.controllers.company_versions import CompanyVersionController
from synthorg.api.controllers.departments.ceremony_policy import (
    DepartmentCeremonyPolicyController,
)
from synthorg.api.controllers.departments.crud import DepartmentController
from synthorg.api.controllers.departments.health import DepartmentHealthController
from synthorg.api.controllers.role_versions import RoleVersionController
from synthorg.api.controllers.teams import TeamController
from synthorg.api.controllers.template_packs import TemplatePackController
from synthorg.meta.mcp.domains.organization import ORGANIZATION_TOOLS
from synthorg.meta.mcp.feature_descriptors import mcp_descriptor
from synthorg.organization.state import OrganizationStateSlice


def _organization_mcp_handlers() -> Mapping[str, object]:
    """Deferred loader for the organization MCP handler map.

    Returns:
        The organization ``{tool_name: ToolHandler}`` map.
    """
    from synthorg.meta.mcp.handlers.organization import (  # noqa: PLC0415
        ORGANIZATION_HANDLERS,
    )

    return ORGANIZATION_HANDLERS


FEATURE: FeatureModule = FeatureManifest(
    name="organization",
    settings_namespace=None,
    state_slice=OrganizationStateSlice,
    controllers=(
        CompanyController,
        CompanyVersionController,
        DepartmentController,
        DepartmentHealthController,
        DepartmentCeremonyPolicyController,
        TeamController,
        RoleVersionController,
        TemplatePackController,
    ),
    mcp_handlers=(
        mcp_descriptor(
            domain="organization",
            tool_defs=ORGANIZATION_TOOLS,
            handlers=_organization_mcp_handlers,
        ),
    ),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
