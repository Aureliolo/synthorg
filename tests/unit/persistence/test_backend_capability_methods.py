"""Tests for ``PersistenceBackend`` capability factory methods.

Locks the contract of ``build_ontology_versioning()`` -- the
capability method introduced by ARC-1 that replaces the
``isinstance(persistence, PostgresPersistenceBackend)`` check in
``api/auto_wire.py``.  Both the SQLite and Postgres backends must
construct a versioning service with the expected surface
(``snapshot_if_changed``, ``force_snapshot``, ``get_latest``) when
invoked on a connected backend.

Postgres coverage lives alongside the existing integration-level
persistence tests; here we exercise SQLite and verify the protocol
surface of the returned service so any signature drift in
``create_ontology_versioning`` surfaces in unit CI.
"""

import inspect

import pytest

from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend
from synthorg.versioning.service import VersioningService

pytestmark = pytest.mark.unit


class TestBuildOntologyVersioning:
    """``build_ontology_versioning()`` returns a wired versioning service."""

    async def test_sqlite_returns_versioning_service(self) -> None:
        """SQLite backend produces a ``VersioningService`` bound to its db."""
        backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:"))
        await backend.connect()
        try:
            service = backend.build_ontology_versioning()
            assert isinstance(service, VersioningService)
            # Each method ``OntologyService`` consumes must exist AND be
            # callable with the expected async signature -- a property,
            # a sync stub, or a renamed method would all fail this check
            # rather than slipping through a bare ``hasattr`` probe.
            for method_name in (
                "snapshot_if_changed",
                "force_snapshot",
                "get_latest",
            ):
                method = getattr(service, method_name)
                assert callable(method), (
                    f"{method_name} must be callable on {service!r}"
                )
                assert inspect.iscoroutinefunction(method), (
                    f"{method_name} must be async"
                )
                # Exercising the signature catches renames of the
                # ``entity_id`` keyword that downstream callers depend on.
                sig = inspect.signature(method)
                assert "entity_id" in sig.parameters, (
                    f"{method_name} must accept an 'entity_id' parameter"
                )
        finally:
            await backend.disconnect()

    async def test_sqlite_factory_not_connected_raises(self) -> None:
        """Calling the factory without ``connect()`` first raises cleanly."""
        from synthorg.core.persistence_errors import (
            PersistenceConnectionError,
        )

        backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:"))
        with pytest.raises(PersistenceConnectionError):
            backend.build_ontology_versioning()
