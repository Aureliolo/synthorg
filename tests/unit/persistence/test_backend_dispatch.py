"""Backend-keyed construction dispatch and the conversational predicate.

``build_for_backend`` replaces open-coded ``if backend.kind == ...``
chains in the wiring helpers: it selects the per-backend factory by the
backend's discriminator and raises ``StrategyFactoryNotFoundError`` for
an unregistered backend. The ``supports_conversational_approvals``
predicate lets the conversational guard ask the backend directly instead
of comparing ``kind`` / ``backend_name`` against a literal.
"""

import pytest
from pydantic import SecretStr

from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.persistence.backend_dispatch import build_for_backend
from synthorg.persistence.config import PostgresConfig, SQLiteConfig
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend
from tests._shared import mock_of

pytestmark = pytest.mark.unit


def test_dispatch_selects_sqlite_factory() -> None:
    backend = mock_of[PersistenceBackend](kind="sqlite")
    result = build_for_backend(
        backend,
        sqlite=lambda: "sqlite-repo",
        postgres=lambda: "postgres-repo",
    )
    assert result == "sqlite-repo"


def test_dispatch_selects_postgres_factory() -> None:
    backend = mock_of[PersistenceBackend](kind="postgres")
    result = build_for_backend(
        backend,
        sqlite=lambda: "sqlite-repo",
        postgres=lambda: "postgres-repo",
    )
    assert result == "postgres-repo"


def test_dispatch_unknown_backend_raises() -> None:
    backend = mock_of[PersistenceBackend](kind="mysql")
    with pytest.raises(StrategyFactoryNotFoundError):
        build_for_backend(
            backend,
            sqlite=lambda: "sqlite-repo",
            postgres=lambda: "postgres-repo",
        )


def test_dispatch_does_not_call_unselected_factory() -> None:
    backend = mock_of[PersistenceBackend](kind="sqlite")

    def _boom() -> str:
        pytest.fail("postgres factory must not run for a sqlite backend")

    assert build_for_backend(backend, sqlite=lambda: "ok", postgres=_boom) == "ok"


def test_sqlite_does_not_support_conversational_approvals() -> None:
    backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:"))
    assert backend.supports_conversational_approvals is False


def test_postgres_supports_conversational_approvals() -> None:
    backend = PostgresPersistenceBackend(
        PostgresConfig(
            database="synthorg",
            username="synthorg",
            password=SecretStr("secret"),
        )
    )
    assert backend.supports_conversational_approvals is True
