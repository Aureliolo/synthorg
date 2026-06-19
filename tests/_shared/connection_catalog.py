"""In-memory ConnectionCatalog helpers for credential-path tests.

Provides a functional dict-backed :class:`SecretBackend` and a factory
that assembles a :class:`ConnectionCatalog` over it plus the in-memory
connection repository stub. Lets tests exercise the catalog-only
provider-credential path (mint-on-create, resolve-at-use) without a real
encrypted backend or a connected persistence layer.
"""

from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.persistence.integration_stubs import InMemoryConnectionRepository


class InMemorySecretBackend:
    """Dict-backed :class:`SecretBackend` for tests.

    Round-trips mint (store) and resolve (retrieve) so the catalog-only
    credential path is exercised end-to-end without encryption.
    """

    def __init__(self) -> None:
        self._secrets: dict[str, bytes] = {}
        self._counter = 0

    @property
    def backend_name(self) -> str:
        return "in-memory-test"

    async def store(self, secret_id: str, value: bytes) -> None:
        self._secrets[secret_id] = value

    async def retrieve(self, secret_id: str) -> bytes | None:
        return self._secrets.get(secret_id)

    async def delete(self, secret_id: str) -> bool:
        return self._secrets.pop(secret_id, None) is not None

    async def rotate(self, old_id: str, new_value: bytes) -> str:
        self._counter += 1
        new_id = f"{old_id}-rot{self._counter}"
        self._secrets[new_id] = new_value
        self._secrets.pop(old_id, None)
        return new_id

    async def close(self) -> None:
        return None


def make_in_memory_catalog() -> ConnectionCatalog:
    """Build a ConnectionCatalog over in-memory repository + secret backend.

    Returns:
        A functional ``ConnectionCatalog`` backed entirely by in-memory
        stubs, suitable for exercising the credential mint/resolve path.
    """
    return ConnectionCatalog(
        repository=InMemoryConnectionRepository(),
        secret_backend=InMemorySecretBackend(),
    )
