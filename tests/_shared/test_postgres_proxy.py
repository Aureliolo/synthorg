"""Unit tests for ``tests/_shared/postgres_proxy.py``.

Direct unit coverage for the env-var bypass logic and the testcontainer
adapter so a regression in defaults, port parsing, or duck-typing is
caught in ~0.1s instead of via the 15-30s integration / conformance
fixtures that consume them.
"""

import pytest

from tests._shared.postgres_proxy import (
    PostgresContainerProxy,
    _PostgresContainerLike,
    from_env,
    from_testcontainer,
)


class _FakePostgresContainer:
    """Typed duck-type mock matching ``_PostgresContainerLike``.

    Used by ``from_testcontainer`` tests so the adapter can be exercised
    without spinning up a real Docker container. Implements the same
    surface as ``testcontainers.postgres.PostgresContainer``.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 55432,
        username: str = "test_user",
        password: str = "test_pass",  # noqa: S107 -- test fixture credential
        dbname: str = "test_db",
    ) -> None:
        self._host = host
        self._port = port
        self.username = username
        self.password = password
        self.dbname = dbname

    def get_container_host_ip(self) -> str:
        return self._host

    def get_exposed_port(self, port: int) -> int:
        del port
        return self._port


@pytest.mark.unit
class TestFromEnv:
    """``from_env`` sentinel, defaults, and port parsing."""

    def test_returns_none_when_host_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in [
            "SYNTHORG_TEST_POSTGRES_HOST",
            "SYNTHORG_TEST_POSTGRES_PORT",
            "SYNTHORG_TEST_POSTGRES_USER",
            "SYNTHORG_TEST_POSTGRES_PASSWORD",
            "SYNTHORG_TEST_POSTGRES_DB",
        ]:
            monkeypatch.delenv(var, raising=False)
        assert from_env() is None

    def test_returns_none_when_host_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SYNTHORG_TEST_POSTGRES_HOST", "")
        assert from_env() is None

    def test_defaults_applied_when_only_host_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SYNTHORG_TEST_POSTGRES_HOST", "ci-host")
        for var in [
            "SYNTHORG_TEST_POSTGRES_PORT",
            "SYNTHORG_TEST_POSTGRES_USER",
            "SYNTHORG_TEST_POSTGRES_PASSWORD",
            "SYNTHORG_TEST_POSTGRES_DB",
        ]:
            monkeypatch.delenv(var, raising=False)
        proxy = from_env()
        assert proxy is not None
        assert proxy.get_container_host_ip() == "ci-host"
        assert proxy.get_exposed_port(5432) == 5432
        assert proxy.username == "synthorg"
        assert proxy.password == "synthorg-test"
        assert proxy.dbname == "synthorg"

    def test_all_env_overrides_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYNTHORG_TEST_POSTGRES_HOST", "10.0.0.5")
        monkeypatch.setenv("SYNTHORG_TEST_POSTGRES_PORT", "6543")
        monkeypatch.setenv("SYNTHORG_TEST_POSTGRES_USER", "custom_user")
        monkeypatch.setenv("SYNTHORG_TEST_POSTGRES_PASSWORD", "custom_pass")
        monkeypatch.setenv("SYNTHORG_TEST_POSTGRES_DB", "custom_db")
        proxy = from_env()
        assert proxy is not None
        assert proxy.get_container_host_ip() == "10.0.0.5"
        assert proxy.get_exposed_port(5432) == 6543
        assert proxy.username == "custom_user"
        assert proxy.password == "custom_pass"
        assert proxy.dbname == "custom_db"

    def test_invalid_port_raises_named_value_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SYNTHORG_TEST_POSTGRES_HOST", "ci-host")
        monkeypatch.setenv("SYNTHORG_TEST_POSTGRES_PORT", "not-a-port")
        with pytest.raises(
            ValueError, match="SYNTHORG_TEST_POSTGRES_PORT='not-a-port'"
        ):
            from_env()


@pytest.mark.unit
class TestFromTestcontainer:
    """``from_testcontainer`` adapts the duck-typed PostgresContainer surface."""

    def test_extracts_all_required_fields(self) -> None:
        container = _FakePostgresContainer(
            host="127.0.0.1",
            port=55432,
            username="test_user",
            password="test_pass",
            dbname="test_db",
        )
        proxy = from_testcontainer(container)
        assert proxy.get_container_host_ip() == "127.0.0.1"
        assert proxy.get_exposed_port(5432) == 55432
        assert proxy.username == "test_user"
        assert proxy.password == "test_pass"
        assert proxy.dbname == "test_db"

    def test_protocol_runtime_check_accepts_compliant_object(self) -> None:
        container = _FakePostgresContainer()
        assert isinstance(container, _PostgresContainerLike)


@pytest.mark.unit
class TestProxyConstructor:
    """``PostgresContainerProxy.__init__`` validation."""

    def test_blank_host_rejected(self) -> None:
        with pytest.raises(ValueError, match="host must be a non-blank"):
            PostgresContainerProxy(
                host="   ", port=5432, username="u", password="p", dbname="d"
            )

    def test_port_below_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="out of valid TCP range"):
            PostgresContainerProxy(
                host="h", port=0, username="u", password="p", dbname="d"
            )

    def test_port_above_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="out of valid TCP range"):
            PostgresContainerProxy(
                host="h", port=70000, username="u", password="p", dbname="d"
            )

    def test_blank_username_rejected(self) -> None:
        with pytest.raises(ValueError, match="username must be a non-blank"):
            PostgresContainerProxy(
                host="h", port=5432, username="", password="p", dbname="d"
            )

    def test_blank_password_rejected(self) -> None:
        with pytest.raises(ValueError, match="password must be a non-blank"):
            PostgresContainerProxy(
                host="h", port=5432, username="u", password=" ", dbname="d"
            )

    def test_blank_dbname_rejected(self) -> None:
        with pytest.raises(ValueError, match="dbname must be a non-blank"):
            PostgresContainerProxy(
                host="h", port=5432, username="u", password="p", dbname=""
            )

    def test_get_exposed_port_ignores_argument(self) -> None:
        proxy = PostgresContainerProxy(
            host="h", port=5432, username="u", password="p", dbname="d"
        )
        assert proxy.get_exposed_port(5432) == 5432
        assert proxy.get_exposed_port(9999) == 5432
