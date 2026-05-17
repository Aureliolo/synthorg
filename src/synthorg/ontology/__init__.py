"""Semantic ontology subsystem for the SynthOrg framework.

Re-exports the public API: models, decorator, protocol, config,
errors, service, and versioning factory.
"""

from synthorg.ontology.config import (
    DelegationGuardConfig,
    DriftDetectionConfig,
    DriftStrategy,
    EntitiesConfig,
    EntityEntry,
    GuardMode,
    InjectionStrategy,
    OntologyConfig,
    OntologyInjectionConfig,
    OntologyMemoryConfig,
    OntologySyncConfig,
)
from synthorg.ontology.decorator import (
    clear_entity_registry,
    get_entity_registry,
    ontology_entity,
)
from synthorg.ontology.errors import (
    OntologyConfigError,
    OntologyConnectionError,
    OntologyDuplicateError,
    OntologyError,
    OntologyNotFoundError,
)
from synthorg.ontology.models import (
    AgentDrift,
    DriftAction,
    DriftReport,
    EntityDefinition,
    EntityField,
    EntityRelation,
    EntitySource,
    EntityTier,
)
from synthorg.ontology.service import OntologyService
from synthorg.persistence.ontology_protocol import OntologyEntityRepository

__all__ = [
    "AgentDrift",
    "DelegationGuardConfig",
    "DriftAction",
    "DriftDetectionConfig",
    "DriftReport",
    "DriftStrategy",
    "EntitiesConfig",
    "EntityDefinition",
    "EntityEntry",
    "EntityField",
    "EntityRelation",
    "EntitySource",
    "EntityTier",
    "GuardMode",
    "InjectionStrategy",
    "OntologyConfig",
    "OntologyConfigError",
    "OntologyConnectionError",
    "OntologyDuplicateError",
    "OntologyEntityRepository",
    "OntologyError",
    "OntologyInjectionConfig",
    "OntologyMemoryConfig",
    "OntologyNotFoundError",
    "OntologyService",
    "OntologySyncConfig",
    "clear_entity_registry",
    "get_entity_registry",
    "ontology_entity",
]
