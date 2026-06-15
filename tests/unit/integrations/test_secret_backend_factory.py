"""Unit tests for the secret backend factory.

Covers the routing contract (which config discriminator produces
which backend) plus the two failure modes added alongside the
``encrypted_postgres`` adapter: missing ``db_path`` for
``encrypted_sqlite`` and missing ``pg_pool`` for
``encrypted_postgres``.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from synthorg.integrations.config import SecretBackendConfig
from synthorg.persistence.secret_backends.encrypted import (
    EncryptedSecretBackend,
)
from synthorg.persistence.secret_backends.env_var import (
    EnvVarSecretBackend,
)
from synthorg.persistence.secret_backends.factory import (
    create_secret_backend,
    resolve_secret_backend_config,
)
from synthorg.persistence.secret_backends.postgres_row_store import (
    PostgresSecretRowStore,
)
from synthorg.persistence.secret_backends.sqlite_row_store import (
    SqliteSecretRowStore,
)


@pytest.mark.unit
class TestFactoryRouting:
    def test_encrypted_sqlite_requires_db_path(self) -> None:
        config = SecretBackendConfig(backend_type="encrypted_sqlite")
        with pytest.raises(
            ValueError, match="db_path is required for encrypted_sqlite"
        ):
            create_secret_backend(config)

    def test_encrypted_postgres_requires_pg_pool(self) -> None:
        config = SecretBackendConfig(backend_type="encrypted_postgres")
        with pytest.raises(
            ValueError, match="pg_pool is required for encrypted_postgres"
        ):
            create_secret_backend(config)

    def test_env_var_needs_no_storage(self) -> None:
        config = SecretBackendConfig(backend_type="env_var")
        backend = create_secret_backend(config)
        assert isinstance(backend, EnvVarSecretBackend)
        assert backend.backend_name == "env_var"

    def test_encrypted_sqlite_constructed_when_key_present(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # A valid Fernet key (44-char url-safe base64 of 32 zero bytes).
        from cryptography.fernet import Fernet

        monkeypatch.setenv("SYNTHORG_MASTER_KEY", Fernet.generate_key().decode())
        config = SecretBackendConfig(backend_type="encrypted_sqlite")
        db_path = str(tmp_path / "secrets.db")
        backend = create_secret_backend(config, db_path=db_path)
        assert isinstance(backend, EncryptedSecretBackend)
        assert isinstance(backend._store, SqliteSecretRowStore)
        assert backend.backend_name == "encrypted_sqlite"

    def test_encrypted_postgres_constructed_when_key_present(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from cryptography.fernet import Fernet

        monkeypatch.setenv("SYNTHORG_MASTER_KEY", Fernet.generate_key().decode())
        config = SecretBackendConfig(backend_type="encrypted_postgres")
        pool = MagicMock()
        backend = create_secret_backend(config, pg_pool=pool)
        assert isinstance(backend, EncryptedSecretBackend)
        assert isinstance(backend._store, PostgresSecretRowStore)
        assert backend.backend_name == "encrypted_postgres"


@pytest.mark.unit
class TestEncryptedPostgresKeyLoading:
    def test_missing_master_key_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from synthorg.integrations.errors import MasterKeyError

        monkeypatch.delenv("SYNTHORG_MASTER_KEY", raising=False)
        pool = MagicMock()
        with pytest.raises(MasterKeyError, match="SYNTHORG_MASTER_KEY is not set"):
            EncryptedSecretBackend(
                PostgresSecretRowStore(pool),
                master_key_env="SYNTHORG_MASTER_KEY",
            )

    def test_invalid_master_key_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from synthorg.integrations.errors import MasterKeyError

        monkeypatch.setenv("SYNTHORG_MASTER_KEY", "not-a-valid-fernet-key")
        pool = MagicMock()
        with pytest.raises(MasterKeyError, match="Invalid Fernet key"):
            EncryptedSecretBackend(
                PostgresSecretRowStore(pool),
                master_key_env="SYNTHORG_MASTER_KEY",
            )


@pytest.mark.unit
class TestResolveSecretBackendConfig:
    """Exercise each branch of the auto-selection ladder in isolation."""

    @pytest.fixture
    def with_master_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from cryptography.fernet import Fernet

        monkeypatch.setenv("SYNTHORG_MASTER_KEY", Fernet.generate_key().decode())

    @pytest.fixture
    def without_master_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SYNTHORG_MASTER_KEY", raising=False)

    def test_default_sqlite_with_db_path_and_key_honoured(
        self,
        with_master_key: None,
    ) -> None:
        selection = resolve_secret_backend_config(
            SecretBackendConfig(),
            postgres_mode=False,
            pg_pool_available=False,
            sqlite_db_path="/tmp/secrets.db",  # noqa: S108
        )
        assert selection.config.backend_type == "encrypted_sqlite"
        assert selection.reason == ""
        assert selection.level == "info"

    def test_default_sqlite_in_postgres_mode_promotes_to_postgres(
        self,
        with_master_key: None,
    ) -> None:
        selection = resolve_secret_backend_config(
            SecretBackendConfig(),
            postgres_mode=True,
            pg_pool_available=True,
            sqlite_db_path=None,
        )
        assert selection.config.backend_type == "encrypted_postgres"
        assert "aligned" in selection.reason
        assert selection.level == "info"

    def test_default_sqlite_in_postgres_mode_no_pool_downgrades(
        self,
        with_master_key: None,
    ) -> None:
        selection = resolve_secret_backend_config(
            SecretBackendConfig(),
            postgres_mode=True,
            pg_pool_available=False,
            sqlite_db_path=None,
        )
        assert selection.config.backend_type == "env_var"
        assert "no pool is available" in selection.reason
        assert selection.level == "error"

    def test_sqlite_without_db_path_downgrades(
        self,
        with_master_key: None,
    ) -> None:
        selection = resolve_secret_backend_config(
            SecretBackendConfig(),
            postgres_mode=False,
            pg_pool_available=False,
            sqlite_db_path=None,
        )
        assert selection.config.backend_type == "env_var"
        assert "no db_path" in selection.reason
        assert selection.level == "error"

    def test_explicit_encrypted_postgres_without_pool_downgrades(
        self,
        with_master_key: None,
    ) -> None:
        selection = resolve_secret_backend_config(
            SecretBackendConfig(backend_type="encrypted_postgres"),
            postgres_mode=True,
            pg_pool_available=False,
            sqlite_db_path=None,
        )
        assert selection.config.backend_type == "env_var"
        assert "no pg_pool" in selection.reason
        assert selection.level == "error"

    def test_missing_master_key_downgrades_sqlite(
        self,
        without_master_key: None,
    ) -> None:
        selection = resolve_secret_backend_config(
            SecretBackendConfig(),
            postgres_mode=False,
            pg_pool_available=False,
            sqlite_db_path="/tmp/secrets.db",  # noqa: S108
        )
        assert selection.config.backend_type == "env_var"
        assert "SYNTHORG_MASTER_KEY is not set" in selection.reason
        assert selection.level == "error"

    def test_missing_master_key_downgrades_postgres(
        self,
        without_master_key: None,
    ) -> None:
        selection = resolve_secret_backend_config(
            SecretBackendConfig(backend_type="encrypted_postgres"),
            postgres_mode=True,
            pg_pool_available=True,
            sqlite_db_path=None,
        )
        assert selection.config.backend_type == "env_var"
        assert "SYNTHORG_MASTER_KEY is not set" in selection.reason
        assert selection.level == "error"

    def test_explicit_env_var_honoured_without_master_key(
        self,
        without_master_key: None,
    ) -> None:
        selection = resolve_secret_backend_config(
            SecretBackendConfig(backend_type="env_var"),
            postgres_mode=True,
            pg_pool_available=True,
            sqlite_db_path=None,
        )
        assert selection.config.backend_type == "env_var"
        assert selection.reason == ""
        assert selection.level == "info"

    def test_custom_master_key_env_respected(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from cryptography.fernet import Fernet

        monkeypatch.delenv("SYNTHORG_MASTER_KEY", raising=False)
        monkeypatch.setenv("MY_CUSTOM_KEY", Fernet.generate_key().decode())
        config = SecretBackendConfig.model_validate(
            {
                "backend_type": "encrypted_sqlite",
                "encrypted_sqlite": {"master_key_env": "MY_CUSTOM_KEY"},
            }
        )
        selection = resolve_secret_backend_config(
            config,
            postgres_mode=False,
            pg_pool_available=False,
            sqlite_db_path="/tmp/secrets.db",  # noqa: S108
        )
        assert selection.config.backend_type == "encrypted_sqlite"
        assert selection.reason == ""
        assert selection.level == "info"

    def test_promotion_preserves_custom_master_key_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Promotion sqlite->postgres must carry the custom master_key_env.

        Without propagation, the promoted encrypted_postgres backend would
        default its master_key_env to "SYNTHORG_MASTER_KEY" and silently
        downgrade to env_var because the operator's key is under a
        different name.
        """
        from cryptography.fernet import Fernet

        monkeypatch.delenv("SYNTHORG_MASTER_KEY", raising=False)
        monkeypatch.setenv("MY_CUSTOM_KEY", Fernet.generate_key().decode())
        config = SecretBackendConfig.model_validate(
            {
                "backend_type": "encrypted_sqlite",
                "encrypted_sqlite": {"master_key_env": "MY_CUSTOM_KEY"},
            }
        )
        selection = resolve_secret_backend_config(
            config,
            postgres_mode=True,
            pg_pool_available=True,
            sqlite_db_path=None,
        )
        assert selection.config.backend_type == "encrypted_postgres"
        assert selection.config.encrypted_postgres.master_key_env == "MY_CUSTOM_KEY"
        assert selection.level == "info"
