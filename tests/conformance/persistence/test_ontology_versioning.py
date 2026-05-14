"""Dual-backend conformance for the ontology versioning factory.

Asserts that both SQLite and Postgres backends produce a working
:class:`VersioningService[EntityDefinition]` via the new
``persistence/<backend>/ontology_versioning.py`` modules (rather than
the previous backend-aware factories living in
``synthorg.ontology.versioning``).
"""

import inspect

import pytest

from synthorg.persistence.protocol import PersistenceBackend
from synthorg.versioning.service import VersioningService

pytestmark = pytest.mark.integration


async def test_build_ontology_versioning_returns_service(
    backend: PersistenceBackend,
) -> None:
    """Each backend's factory yields a VersioningService bound to its db."""
    service = backend.build_ontology_versioning()
    assert isinstance(service, VersioningService)
    for method_name in ("snapshot_if_changed", "force_snapshot", "get_latest"):
        method = getattr(service, method_name)
        assert callable(method), f"{method_name} must be callable on {service!r}"
        assert inspect.iscoroutinefunction(method), f"{method_name} must be async"
        sig = inspect.signature(method)
        assert "entity_id" in sig.parameters, (
            f"{method_name} must accept an 'entity_id' parameter"
        )
