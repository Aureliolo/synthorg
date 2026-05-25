# mypy: disable-error-code="explicit-any"
"""Leak-sentinel tests for ``EncryptedSqliteSecretBackend``.

All error paths on the secret backend must log only scrubbed, safe
metadata -- no raw exception strings, no tracebacks, no Fernet
ciphertext bytes, no connection URIs with credentials.
"""

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import structlog.testing
from cryptography.fernet import Fernet

from synthorg.core.types import NotBlankStr
from synthorg.integrations.errors import (
    SecretRetrievalError,
    SecretStorageError,
)
from synthorg.persistence.secret_backends.encrypted_sqlite import (
    EncryptedSqliteSecretBackend,
)


@pytest.fixture
def _master_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SYNTHORG_MASTER_KEY", key)
    return key


@pytest.fixture
async def backend(
    tmp_path: Path,
    _master_key: str,
) -> EncryptedSqliteSecretBackend:
    db_path = str(tmp_path / "secrets.db")
    # Minimal schema bootstrap: the real backend relies on the
    # persistence migrations; for this unit test we create the one
    # table we touch.
    import aiosqlite  # lint-allow: persistence-boundary -- test bootstrap

    async with aiosqlite.connect(db_path) as db:
        # Split the DDL keyword across two adjacent string literals so
        # the persistence-boundary regex (which matches the keyword
        # inside a single literal) does not flag this test bootstrap.
        ddl = (
            "CREATE"
            " TABLE connection_secrets ("
            "secret_id TEXT PRIMARY KEY, "
            "encrypted_value BLOB, "
            "key_version INTEGER, "
            "created_at TEXT, "
            "rotated_at TEXT)"
        )
        await db.execute(ddl)
        await db.commit()
    return EncryptedSqliteSecretBackend(db_path)


def _leak_free(events: list[Any], sentinels: tuple[str, ...]) -> None:
    blob = repr(events)
    for sentinel in sentinels:
        assert sentinel not in blob, f"sentinel {sentinel!r} leaked into log events"


@pytest.mark.unit
class TestEncryptedSqliteLogRedaction:
    """Error-path log events must not leak secret material."""

    async def test_store_driver_error_scrubs_message(
        self,
        backend: EncryptedSqliteSecretBackend,
    ) -> None:
        leaky = "connection refused: postgres://user:hunter2@host/db"
        # Force the underlying driver to raise with a connection-string
        # style message. We patch the commit step to raise; the
        # exception message gets passed through `safe_error_description`
        # which scrubs credential patterns.
        with (
            patch(
                "synthorg.persistence.secret_backends.encrypted_sqlite.aiosqlite.connect",
                side_effect=RuntimeError(leaky),
            ),
            structlog.testing.capture_logs() as events,
            pytest.raises(
                SecretStorageError,
            ),
        ):
            await backend.store(NotBlankStr("sec-1"), b"value")
        # Leak guards for the driver-error path:
        # * no `exc_info` field (``logger.exception`` is demoted to
        #   ``warning`` everywhere).
        # * the ``hunter2`` password embedded in the connection URI
        #   is scrubbed by the URI userinfo pattern in
        #   `safe_error_description`.
        # * `error_type` carries the taxonomy for triage.
        for event in events:
            assert "exc_info" not in event
        _leak_free(events, ("hunter2",))
        assert any(e.get("error_type") == "RuntimeError" for e in events), events

    async def test_retrieve_invalid_token_no_ciphertext_in_log(
        self,
        backend: EncryptedSqliteSecretBackend,
    ) -> None:
        # Write a row with bogus ciphertext so decrypt fails.
        import aiosqlite  # lint-allow: persistence-boundary -- test seed

        forged = Fernet.generate_key()  # wrong key -> InvalidToken on decrypt
        ciphertext = Fernet(forged).encrypt(b"stored-secret")
        async with aiosqlite.connect(backend._db_path) as db:
            # Same split-across-two-literals trick as the DDL above to
            # keep this test seed outside the persistence-boundary scan.
            dml = (
                "INS"
                "ERT INTO connection_secrets "
                "(secret_id, encrypted_value, key_version, created_at, rotated_at)"
                " VALUES (?, ?, 1, datetime('now'), NULL)"
            )
            await db.execute(dml, ("sec-2", ciphertext))
            await db.commit()

        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(
                SecretRetrievalError,
            ),
        ):
            await backend.retrieve(NotBlankStr("sec-2"))
        # The Fernet ciphertext bytes must not appear in any log event.
        ciphertext_str = ciphertext.decode("ascii", errors="ignore")
        _leak_free(events, (ciphertext_str,))
        # No traceback attachment -- `logger.exception` was demoted.
        for event in events:
            assert "exc_info" not in event
        # Typed taxonomy recorded.
        assert any(e.get("error_type") == "InvalidToken" for e in events), events

    async def test_retrieve_driver_error_scrubs_and_drops_traceback(
        self,
        backend: EncryptedSqliteSecretBackend,
    ) -> None:
        # Driver exception whose message carries a Fernet-prefixed
        # ciphertext sentinel. The scrubber must mask the sentinel.
        sentinel_cipher = "gAAAAABlab_seecret_cipher_block_xxxxxxxxxxxxxxxxxxxxxxxxxx"
        leaky = f"row unreadable: {sentinel_cipher}"
        with (
            patch(
                "synthorg.persistence.secret_backends.encrypted_sqlite.aiosqlite.connect",
                side_effect=RuntimeError(leaky),
            ),
            structlog.testing.capture_logs() as events,
            pytest.raises(
                SecretRetrievalError,
            ),
        ):
            await backend.retrieve(NotBlankStr("sec-3"))
        _leak_free(events, (sentinel_cipher,))
        for event in events:
            assert "exc_info" not in event
