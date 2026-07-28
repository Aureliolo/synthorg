"""An absent connection is a routine answer, not a fault.

``get_credentials`` raises (and every caller that considers absence
exceptional reports it with its own context), so the catalog must not also
warn on the operator's behalf: an optional integration nobody configured is
asked about on every dashboard poll.
"""

import pytest
from structlog.testing import capture_logs

from synthorg.integrations.connections.models import AuthMethod, ConnectionType
from synthorg.integrations.errors import ConnectionNotFoundError
from synthorg.observability.events.integrations import CONNECTION_NOT_FOUND
from tests._shared.connection_catalog import make_in_memory_catalog

pytestmark = pytest.mark.unit


class TestQuietCredentialLookup:
    async def test_returns_none_for_an_absent_connection(self) -> None:
        catalog = make_in_memory_catalog()

        assert await catalog.get_credentials_or_none("never-created") is None

    async def test_absent_connection_does_not_warn(self) -> None:
        catalog = make_in_memory_catalog()

        with capture_logs() as logs:
            await catalog.get_credentials_or_none("never-created")

        assert not [
            entry
            for entry in logs
            if entry.get("event") == CONNECTION_NOT_FOUND
            and entry.get("log_level") == "warning"
        ], f"a routine absence warned (logs={logs})"

    async def test_resolves_credentials_when_present(self) -> None:
        catalog = make_in_memory_catalog()
        await catalog.create(
            name="example-api",
            connection_type=ConnectionType.GENERIC_HTTP,
            auth_method=AuthMethod.API_KEY.value,
            credentials={"base_url": "https://api.example.test", "token": "secret"},
        )

        creds = await catalog.get_credentials_or_none("example-api")

        assert creds is not None
        assert creds["token"] == "secret"

    async def test_raising_variant_still_raises(self) -> None:
        catalog = make_in_memory_catalog()

        with pytest.raises(ConnectionNotFoundError):
            await catalog.get_credentials("never-created")
