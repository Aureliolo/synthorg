"""In-memory repository implementations for integration tables.

.. note::
    The production ``SQLitePersistenceBackend`` and
    ``PostgresPersistenceBackend`` now wire in durable connection,
    connection-secret, OAuth state, and webhook-receipt repositories
    directly (``synthorg.persistence.{sqlite,postgres}.<repo>_repo``).
    These ``InMemory*`` classes remain available for **unit-test
    fakes** that don't want to spin up a real database.

.. warning::
    Process-local, non-durable. Data lives only in the current
    Python process, is not replicated across replicas, and is lost
    on restart. **Never** wire these into a production backend.

All reads return deep copies so callers cannot mutate internal
state by holding references to returned models. Even though the
domain models are frozen Pydantic ``BaseModel`` instances, their
mutable fields (``dict`` metadata) would otherwise still be
aliased to the stored value.
"""

import copy

from synthorg.integrations.connections.models import (
    Connection,  # noqa: TC001
    ConnectionType,  # noqa: TC001
    OAuthState,  # noqa: TC001
    WebhookReceipt,  # noqa: TC001
)


class InMemoryConnectionRepository:
    """In-memory ``ConnectionRepository`` implementation."""

    def __init__(self) -> None:
        self._store: dict[str, Connection] = {}

    async def save(self, connection: Connection) -> None:
        """Persist a connection (deep-copied on write)."""
        self._store[connection.name] = copy.deepcopy(connection)

    async def get(self, name: str) -> Connection | None:
        """Retrieve by name (deep-copied on read)."""
        existing = self._store.get(name)
        return copy.deepcopy(existing) if existing is not None else None

    async def list_all(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[Connection, ...]:
        """List all (deep-copied)."""
        rows = tuple(
            copy.deepcopy(c) for c in sorted(self._store.values(), key=lambda c: c.name)
        )
        effective_offset = max(0, int(offset))
        if limit is None:
            return rows[effective_offset:]
        return rows[effective_offset : effective_offset + max(0, int(limit))]

    async def list_by_type(
        self,
        connection_type: ConnectionType,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[Connection, ...]:
        """List by type (deep-copied)."""
        matches = tuple(
            copy.deepcopy(c)
            for c in sorted(self._store.values(), key=lambda c: c.name)
            if c.connection_type == connection_type
        )
        effective_offset = max(0, int(offset))
        if limit is None:
            return matches[effective_offset:]
        return matches[effective_offset : effective_offset + max(0, int(limit))]

    async def delete(self, name: str) -> bool:
        """Delete by name."""
        return self._store.pop(name, None) is not None


class InMemoryConnectionSecretRepository:
    """In-memory ``ConnectionSecretRepository`` implementation."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    async def store(
        self,
        secret_id: str,
        encrypted_value: bytes,
        key_version: int,  # noqa: ARG002
    ) -> None:
        """Persist a secret (bytes are immutable, no copy needed)."""
        self._store[secret_id] = encrypted_value

    async def retrieve(self, secret_id: str) -> bytes | None:
        """Retrieve a secret."""
        return self._store.get(secret_id)

    async def delete(self, secret_id: str) -> bool:
        """Delete a secret."""
        return self._store.pop(secret_id, None) is not None


class InMemoryOAuthStateRepository:
    """In-memory ``OAuthStateRepository`` implementation."""

    def __init__(self) -> None:
        self._store: dict[str, OAuthState] = {}

    async def save(self, state: OAuthState) -> None:
        """Persist a state (deep-copied)."""
        self._store[state.state_token] = copy.deepcopy(state)

    async def get(self, state_token: str) -> OAuthState | None:
        """Retrieve by token (deep-copied)."""
        existing = self._store.get(state_token)
        return copy.deepcopy(existing) if existing is not None else None

    async def delete(self, state_token: str) -> bool:
        """Delete by token."""
        return self._store.pop(state_token, None) is not None

    async def cleanup_expired(self) -> int:
        """In-memory: no durable TTL enforcement; callers may run GC."""
        return 0


class InMemoryWebhookReceiptRepository:
    """In-memory ``WebhookReceiptRepository`` implementation."""

    def __init__(self) -> None:
        self._store: list[WebhookReceipt] = []

    async def log(self, receipt: WebhookReceipt) -> None:
        """Persist a receipt (deep-copied)."""
        self._store.append(copy.deepcopy(receipt))

    async def get_by_connection(
        self,
        connection_name: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[WebhookReceipt, ...]:
        """List by connection (deep-copied), newest-first."""
        if limit <= 0:
            return ()
        effective_offset = max(0, int(offset))
        # ``self._store`` is append-ordered, so the most recent
        # receipts live at the end. The repository contract asks
        # callers to receive newest-first, so reverse before slicing.
        matches = [
            copy.deepcopy(r)
            for r in reversed(self._store)
            if r.connection_name == connection_name
        ]
        return tuple(matches[effective_offset : effective_offset + int(limit)])

    async def cleanup_old(
        self,
        retention_days: int,  # noqa: ARG002
    ) -> int:
        """In-memory: no retention policy; callers may truncate."""
        return 0
