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
from typing import Any


class PostgresContainerProxy:
    """Connection-info handle with the testcontainers subset surface.

    Provides ``get_container_host_ip``, ``get_exposed_port``, and the
    ``username`` / ``password`` / ``dbname`` attributes that consumers
    of the Postgres fixtures touch. Construction is keyword-only; both
    ``from_env`` and ``from_testcontainer`` factories build instances
    so callers never instantiate it directly.
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
        self._host = host
        self._port = port
        self.username = username
        self.password = password
        self.dbname = dbname

    def get_container_host_ip(self) -> str:
        return self._host

    def get_exposed_port(self, _internal_port: int) -> str:
        return str(self._port)


def from_env() -> PostgresContainerProxy | None:
    """Build a proxy from ``SYNTHORG_TEST_POSTGRES_*`` env vars.

    Returns ``None`` when ``SYNTHORG_TEST_POSTGRES_HOST`` is unset
    (local dev). The remaining env vars are read with sensible
    defaults that match the values the CI service container is
    configured with; ``HOST`` is the only sentinel because a CI run
    that forgot to publish the others is a misconfiguration that
    should fail loudly on first DB connect, not be papered over.
    """
    host = os.environ.get("SYNTHORG_TEST_POSTGRES_HOST")
    if not host:
        return None
    return PostgresContainerProxy(
        host=host,
        port=int(os.environ.get("SYNTHORG_TEST_POSTGRES_PORT", "5432")),
        username=os.environ.get("SYNTHORG_TEST_POSTGRES_USER", "synthorg"),
        password=os.environ.get("SYNTHORG_TEST_POSTGRES_PASSWORD", "synthorg-test"),
        dbname=os.environ.get("SYNTHORG_TEST_POSTGRES_DB", "synthorg"),
    )


def from_testcontainer(container: Any) -> PostgresContainerProxy:
    """Adapt a ``testcontainers.postgres.PostgresContainer`` to the proxy.

    Argument is typed ``Any`` to avoid importing ``testcontainers`` at
    module-import time; tests that use this path already have
    ``testcontainers`` installed via the test extra. The required
    surface (``get_container_host_ip``, ``get_exposed_port``,
    ``username``, ``password``, ``dbname``) is duck-typed.
    """
    return PostgresContainerProxy(
        host=container.get_container_host_ip(),
        port=int(container.get_exposed_port(5432)),
        username=container.username,
        password=container.password,
        dbname=container.dbname,
    )
