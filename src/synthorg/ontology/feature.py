# module-kind: feature
"""Ontology (semantic vocabulary) feature manifest.

Declares the ontology feature's surface: its state slice and REST
controller. The ontology service is wired at boot after persistence
connects; the feature has no dedicated settings namespace or MCP domain.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.ontology.admin import OntologyAdminController
from synthorg.api.controllers.ontology.drift import OntologyDriftController
from synthorg.api.controllers.ontology.entities import OntologyController
from synthorg.api.controllers.ontology.versions import OntologyVersionsController
from synthorg.ontology.state import OntologyStateSlice

FEATURE: FeatureModule = FeatureManifest(
    name="ontology",
    settings_namespace=None,
    state_slice=OntologyStateSlice,
    controllers=(
        OntologyController,
        OntologyVersionsController,
        OntologyDriftController,
        OntologyAdminController,
    ),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=("build_drift_detection_service",),
    depends_on=(),
)
