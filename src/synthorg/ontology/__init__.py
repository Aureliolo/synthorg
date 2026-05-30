"""Semantic ontology subsystem public API.

``OntologyService`` and ``OntologyEntityRepository`` are exported
lazily (PEP 562) so importing the lightweight ``@ontology_entity``
decorator does not pull ``persistence`` -> ``security`` at package
import time; that eager edge closes a cross-package import cycle.
``from synthorg.ontology import OntologyService`` still works,
resolved and cached on first access.
"""

import threading
from typing import TYPE_CHECKING, Final

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


_LAZY_EXPORT_LOCK: Final[threading.Lock] = threading.Lock()


def __getattr__(name: str) -> object:
    """Resolve and cache a lazily-exported symbol on first access (PEP 562).

    The cache write into ``globals()`` is guarded so concurrent
    first-access from multiple threads cannot double-import the heavy
    submodule or overwrite the cached object mid-write (mirrors
    :mod:`synthorg.tools.mcp`).

    Returns:
        The resolved (and now cached) export object for ``name``.

    Raises:
        AttributeError: When ``name`` is not a known lazy export.
    """
    if name not in _LAZY_EXPORTS:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    import importlib  # noqa: PLC0415

    with _LAZY_EXPORT_LOCK:
        if name in globals():
            return globals()[name]
        module_path, attr = _LAZY_EXPORTS[name]
        value = getattr(importlib.import_module(module_path), attr)
        globals()[name] = value
        return value


def __dir__() -> list[str]:
    """Include the lazily-exported names in ``dir()`` / autocomplete.

    Returns:
        The sorted list of public export names.
    """
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
