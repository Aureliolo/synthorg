"""Fixtures for integration tests of the integrations subsystem."""

import asyncio
import sys
from collections.abc import AsyncGenerator, Callable, Generator, Mapping

import pytest

from synthorg.communication.bus.memory import InMemoryMessageBus
from synthorg.communication.config import MessageBusConfig

# Re-export Postgres integration fixtures so tests in this directory can
# mount a real Postgres testcontainer via ``postgres_backend``. pytest
# resolves fixtures by their module-level names, so importing the
# fixture functions here makes them visible to every test below this
# conftest -- without needing the forbidden ``pytest_plugins`` in a
# non-root conftest. pytest_asyncio_loop_factories is a pluggy hook
# rather than a fixture and is registered inline below (re-exporting a
# hook via ``import`` does not register it with pluggy).
from tests.integration.persistence.conftest import (  # noqa: F401
    postgres_backend,
    postgres_container,
)

if sys.platform == "win32":  # pragma: no cover -- Windows-only branch

    def pytest_asyncio_loop_factories(
        config: pytest.Config,
        item: pytest.Item,
    ) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
        """Use ``SelectorEventLoop`` on Windows so psycopg async mode works.

        Mirrors the hook in ``tests/integration/persistence/conftest.py``;
        the Postgres backend relies on ``SelectorEventLoop`` for psycopg
        async mode.
        """
        return {"selector": asyncio.SelectorEventLoop}


# Fixed valid Fernet key so PKCE verifier encrypt/decrypt works in
# every integration test that exercises the authorization code flow.
_TEST_MASTER_KEY = "lKzZcMznksIF8A_2HFFUnKxhxhz9_bxTvVJoZ6mvZrk="


@pytest.fixture(autouse=True)
def _set_master_key(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None]:
    """Set ``SYNTHORG_MASTER_KEY`` so the PKCE verifier cipher can init.

    The OAuth authorization code flow now encrypts the PKCE verifier
    at rest using Fernet, keyed by ``SYNTHORG_MASTER_KEY``. Tests that
    do not explicitly set the env var would otherwise fail during
    the token exchange with ``MasterKeyError``. Also reset the cached
    cipher between tests so a stale holder from a previous run does
    not leak into the current one.
    """
    from synthorg.integrations.oauth.pkce import _reset_cipher_for_tests

    monkeypatch.setenv("SYNTHORG_MASTER_KEY", _TEST_MASTER_KEY)
    _reset_cipher_for_tests()
    yield
    _reset_cipher_for_tests()


@pytest.fixture
async def memory_bus() -> AsyncGenerator[InMemoryMessageBus]:
    """Create and start an InMemoryMessageBus with integration channels."""
    config = MessageBusConfig(
        channels=(
            "#webhooks",
            "#ratelimit",
        ),
    )
    bus = InMemoryMessageBus(config=config)
    await bus.start()
    yield bus
    await bus.stop()
