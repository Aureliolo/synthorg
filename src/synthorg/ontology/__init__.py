"""Semantic ontology subsystem for the SynthOrg framework.

Re-exports the public API: models, decorator, protocol, config,
errors, service, and versioning factory.

``OntologyService`` and ``OntologyEntityRepository`` are exported
lazily (PEP 562 ``__getattr__``). Eagerly importing them here pulled
``synthorg.persistence`` -> ``synthorg.security`` at ontology-package
import time, which closed a cross-package cycle: a low-level leaf
model (``budget.cost_record``) only needs the lightweight
``@ontology_entity`` decorator, but importing it ran this package
``__init__`` and dragged persistence/security/providers, deadlocking
whenever ``synthorg.client`` (or any consumer) was imported before
``synthorg.api.app``. The public API is unchanged: ``from
synthorg.ontology import OntologyService`` still works, resolved on
first access.
"""

from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from synthorg.ontology.service import OntologyService
    from synthorg.persistence.ontology_protocol import OntologyEntityRepository

# name -> (module path, attribute) for PEP 562 lazy resolution.
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "OntologyService": ("synthorg.ontology.service", "OntologyService"),
    "OntologyEntityRepository": (
        "synthorg.persistence.ontology_protocol",
        "OntologyEntityRepository",
    ),
}


def __getattr__(name: str) -> object:
    """Resolve lazily-exported symbols on first access (PEP 562)."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    import importlib  # noqa: PLC0415

    module_path, attr = target
    value = getattr(importlib.import_module(module_path), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Include the lazily-exported names in ``dir()`` / autocomplete."""
    return sorted(__all__)


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
