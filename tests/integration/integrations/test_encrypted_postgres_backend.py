"""Integration tests for the encrypted_postgres secret backend.

Exercises the Fernet-ciphertext-in-Postgres adapter against a real
Postgres 18 container via testcontainers, using the shared
``postgres_backend`` fixture so we pick up the migrated
``connection_secrets`` table.
"""

import pytest
from cryptography.fernet import Fernet

from synthorg.integrations.errors import (
    SecretRetrievalError,
    SecretRotationError,
    SecretStorageError,
)
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend
from synthorg.persistence.secret_backends.encrypted import (
    EncryptedSecretBackend,
)
from synthorg.persistence.secret_backends.postgres_row_store import (
    PostgresSecretRowStore,
)


def _make_postgres_backend(pool: object) -> EncryptedSecretBackend:
    """Compose the encrypted backend over a Postgres row store."""
    return EncryptedSecretBackend(
        PostgresSecretRowStore(pool),  # type: ignore[arg-type]
        master_key_env="SYNTHORG_MASTER_KEY",
    )


@pytest.fixture
def master_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("SYNTHORG_MASTER_KEY", key)
    return key


@pytest.fixture
async def encrypted_postgres(
    postgres_backend: PostgresPersistenceBackend,
    master_key: str,
) -> EncryptedSecretBackend:
    pool = postgres_backend.get_db()
    return _make_postgres_backend(pool)


@pytest.mark.integration
class TestEncryptedPostgresRoundTrip:
    async def test_store_and_retrieve(
        self,
        encrypted_postgres: EncryptedSecretBackend,
    ) -> None:
        await encrypted_postgres.store("secret-a", b"super-secret-token")
        got = await encrypted_postgres.retrieve("secret-a")
        assert got == b"super-secret-token"

    async def test_retrieve_missing_returns_none(
        self,
        encrypted_postgres: EncryptedSecretBackend,
    ) -> None:
        assert await encrypted_postgres.retrieve("no-such-secret") is None

    async def test_store_overwrites_existing(
        self,
        encrypted_postgres: EncryptedSecretBackend,
    ) -> None:
        await encrypted_postgres.store("secret-b", b"first")
        await encrypted_postgres.store("secret-b", b"second")
        got = await encrypted_postgres.retrieve("secret-b")
        assert got == b"second"

    async def test_delete_removes_secret(
        self,
        encrypted_postgres: EncryptedSecretBackend,
    ) -> None:
        await encrypted_postgres.store("secret-c", b"to-be-deleted")
        assert await encrypted_postgres.delete("secret-c") is True
        assert await encrypted_postgres.retrieve("secret-c") is None
        assert await encrypted_postgres.delete("secret-c") is False

    async def test_rotate_produces_new_id(
        self,
        encrypted_postgres: EncryptedSecretBackend,
    ) -> None:
        await encrypted_postgres.store("secret-d", b"old-value")
        new_id = await encrypted_postgres.rotate("secret-d", b"new-value")
        assert new_id != "secret-d"
        assert await encrypted_postgres.retrieve("secret-d") is None
        assert await encrypted_postgres.retrieve(new_id) == b"new-value"

    async def test_rotate_missing_old_id_rolls_back(
        self,
        encrypted_postgres: EncryptedSecretBackend,
    ) -> None:
        """If old_id doesn't exist, the newly written secret is rolled back."""
        with pytest.raises(SecretRotationError, match="not found during rotation"):
            await encrypted_postgres.rotate("no-such-old-id", b"new-value")

    async def test_rotate_store_failure_wraps_error(
        self,
        encrypted_postgres: EncryptedSecretBackend,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If storing the new secret fails, rotation raises SecretRotationError."""

        async def _fail_store(*_args: object, **_kwargs: object) -> None:
            msg = "simulated store failure"
            raise SecretStorageError(msg)

        monkeypatch.setattr(encrypted_postgres, "store", _fail_store)
        with pytest.raises(SecretRotationError, match="Failed to store rotated secret"):
            await encrypted_postgres.rotate("irrelevant", b"new-value")


@pytest.mark.integration
class TestEncryptedPostgresKeyMismatch:
    async def test_wrong_key_fails_loudly(
        self,
        postgres_backend: PostgresPersistenceBackend,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Ciphertext written with key A must not decrypt under key B."""
        pool = postgres_backend.get_db()
        key_a = Fernet.generate_key().decode("ascii")
        monkeypatch.setenv("SYNTHORG_MASTER_KEY", key_a)
        writer = _make_postgres_backend(pool)
        await writer.store("keyed-secret", b"payload")

        key_b = Fernet.generate_key().decode("ascii")
        assert key_a != key_b
        monkeypatch.setenv("SYNTHORG_MASTER_KEY", key_b)
        reader = _make_postgres_backend(pool)
        with pytest.raises(SecretRetrievalError, match="Failed to decrypt"):
            await reader.retrieve("keyed-secret")


@pytest.mark.integration
class TestEncryptedPostgresCiphertextAtRest:
    async def test_ciphertext_is_not_plaintext(
        self,
        encrypted_postgres: EncryptedSecretBackend,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        """The raw bytes in the DB column must not contain the plaintext."""
        plaintext = b"this-should-not-be-readable-via-sql"
        await encrypted_postgres.store("secret-e", plaintext)

        pool = postgres_backend.get_db()
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT encrypted_value FROM connection_secrets WHERE secret_id = %s",
                ("secret-e",),
            )
            row = await cur.fetchone()
        assert row is not None
        ciphertext = bytes(row[0])
        assert plaintext not in ciphertext
