# module-kind: feature
"""Ontology (semantic vocabulary) feature manifest.

Declares the ontology feature's surface: its state slice and REST
controller. The ontology service is wired at boot after persistence
connects; the feature has no dedicated settings namespace, MCP domain, or
ghost-wired symbols.
"""

from synthorg._core.features import FeatureManifest, FeatureModule
from synthorg.api.controllers.ontology import OntologyController
from synthorg.ontology.state import OntologyStateSlice

FEATURE: FeatureModule = FeatureManifest(
    name="ontology",
    settings_namespace=None,
    state_slice=OntologyStateSlice,
    controllers=(OntologyController,),
    mcp_handlers=(),
    lifecycle_hooks=(),
    ghost_wired_symbols=(),
    depends_on=(),
)
