"""Connection-info proxy for Postgres fixtures.

``PostgresContainerProxy`` exposes the subset of
``testcontainers.postgres.PostgresContainer`` that integration and
conformance tests actually consume (host, port, credentials, db name).
Tests yield a proxy regardless of whether the underlying Postgres comes
from a local testcontainers-managed Docker container or a CI service
container (``services: postgres`` in GitHub Actions).

In CI the GitHub-hosted ``services: postgres`` block populates
``SYNTHORG_TEST_POSTGRES_HOST`` / ``PORT`` / ``USER`` / ``PASSWORD`` /
``DB`` env vars before any test code runs. ``from_env`` returns a
ready-built proxy when those env vars are set so the conftest can skip
the testcontainers start-up dance entirely. Local-dev test runs leave
those env vars unset and fall back to ``from_testcontainer``.
"""

import os
from typing import Protocol, runtime_checkable

_MIN_PORT: int = 1
_MAX_PORT: int = 65535


@runtime_checkable
class _PostgresContainerLike(Protocol):
    """Duck-typed surface of ``testcontainers.postgres.PostgresContainer``.

    Declared as a ``runtime_checkable`` Protocol so ``from_testcontainer``
    can document the contract without importing ``testcontainers`` at
    module-import time. Only the attributes/methods the fixture path
    consumes are required; anything else on the real PostgresContainer
    is irrelevant to the proxy adapter.
    """

    username: str
    password: str
    dbname: str

    def get_container_host_ip(self) -> str: ...

    def get_exposed_port(self, port: int) -> int: ...


class PostgresContainerProxy:
    """Connection-info handle with the testcontainers subset surface.

    Provides ``get_container_host_ip``, ``get_exposed_port``, and the
    ``username`` / ``password`` / ``dbname`` attributes that consumers
    of the Postgres fixtures touch. Construction is keyword-only; both
    ``from_env`` and ``from_testcontainer`` factories build instances
    so callers never instantiate it directly.

    Construction validates port is in the valid TCP range and that all
    credentials are non-blank, so a misconfigured CI run fails at
    fixture setup with an actionable error instead of a downstream
    "could not connect" timeout that names nothing.
    """

    __slots__ = ("_host", "_port", "dbname", "password", "username")

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        dbname: str,
    ) -> None:
        if not host or not host.strip():
            msg = "PostgresContainerProxy: host must be a non-blank string"
            raise ValueError(msg)
        if not _MIN_PORT <= port <= _MAX_PORT:
            msg = (
                f"PostgresContainerProxy: port {port!r} out of valid TCP range "
                f"[{_MIN_PORT}, {_MAX_PORT}]"
            )
            raise ValueError(msg)
        if not username or not username.strip():
            msg = "PostgresContainerProxy: username must be a non-blank string"
            raise ValueError(msg)
        if not password or not password.strip():
            msg = "PostgresContainerProxy: password must be a non-blank string"
            raise ValueError(msg)
        if not dbname or not dbname.strip():
            msg = "PostgresContainerProxy: dbname must be a non-blank string"
            raise ValueError(msg)
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


def from_env() -> PostgresContainerProxy | None:
    """Build a proxy from ``SYNTHORG_TEST_POSTGRES_*`` env vars.

    Returns ``None`` when ``SYNTHORG_TEST_POSTGRES_HOST`` is unset
    (local dev). The remaining env vars are read with sensible
    defaults that match the values the CI service container is
    configured with; ``HOST`` is the only sentinel because a CI run
    that forgot to publish the others is a misconfiguration that
    should fail loudly on first DB connect, not be papered over.

    Raises ``ValueError`` when ``SYNTHORG_TEST_POSTGRES_PORT`` is set
    to a non-numeric value, naming the offending env var so the
    operator can fix it without trawling a stack trace.
    """
    host = os.environ.get("SYNTHORG_TEST_POSTGRES_HOST")
    if not host:
        return None
    port_raw = os.environ.get("SYNTHORG_TEST_POSTGRES_PORT", "5432")
    try:
        port = int(port_raw)
    except ValueError as exc:
        msg = (
            f"SYNTHORG_TEST_POSTGRES_PORT={port_raw!r} is not a valid integer; "
            f"expected a TCP port number (e.g. '5432')."
        )
        raise ValueError(msg) from exc
    return PostgresContainerProxy(
        host=host,
        port=port,
        username=os.environ.get("SYNTHORG_TEST_POSTGRES_USER", "synthorg"),
        password=os.environ.get("SYNTHORG_TEST_POSTGRES_PASSWORD", "synthorg-test"),
        dbname=os.environ.get("SYNTHORG_TEST_POSTGRES_DB", "synthorg"),
    )


def from_testcontainer(container: _PostgresContainerLike) -> PostgresContainerProxy:
    """Adapt a ``testcontainers.postgres.PostgresContainer`` to the proxy.

    Callers pass a live ``PostgresContainer`` instance; the adapter
    extracts the connection info via the documented surface
    (``get_container_host_ip``, ``get_exposed_port``, ``username``,
    ``password``, ``dbname``) and returns a proxy that downstream
    fixtures can yield instead of the real container. Avoiding the
    real container removes a per-process Docker SDK handle and
    decouples consumers from the testcontainers package.

    The parameter is typed as ``_PostgresContainerLike`` (a
    ``runtime_checkable`` Protocol) so type-checkers verify the
    duck-typed surface without forcing a module-level
    ``import testcontainers``.
    """
    return PostgresContainerProxy(
        host=container.get_container_host_ip(),
        port=container.get_exposed_port(5432),
        username=container.username,
        password=container.password,
        dbname=container.dbname,
    )
