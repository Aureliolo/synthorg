"""Writing one capability source's setting through the API.

The write is a full replace of that source's entry, so what an omitted
field means is a real decision rather than a detail: a caller toggling
``enabled`` sends no ``feed_url``, and must not thereby discard the URL an
operator configured.
"""

import pytest

from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import RootConfig
from synthorg.providers.capability_sources.registry import list_capability_sources
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import (
    FakeMessageBus,
    FakePersistenceBackend,
    make_auth_headers,
)

pytestmark = pytest.mark.unit

_HEADERS = make_auth_headers("ceo")
_BASE = "/api/v1/providers/capability-sources"
_CUSTOM_URL = "https://feeds.example/operator-chosen.csv"


def _a_registered_label() -> str:
    """Return a label the registry declares.

    Returns:
        The first registered source's label, so the test follows the
        shipped registry rather than restating it.
    """
    return str(next(iter(list_capability_sources())).label)


def _build_client(
    *,
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> LoopAsyncClient:
    """Build a client with settings persistence wired.

    Returns:
        A client whose app can read and write the sources setting.
    """
    from synthorg.api.auth.service import AuthService
    from tests._shared import build_test_app as create_app
    from tests.unit.api.conftest import (
        _make_test_auth_service,
        _seed_test_users,
    )

    auth_service: AuthService = _make_test_auth_service()
    _seed_test_users(fake_persistence, auth_service)
    app = create_app(
        config=RootConfig(company_name="test"),
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=CostTracker(),
        auth_service=auth_service,
        settings_service=SettingsService(
            repository=fake_persistence.settings, registry=get_registry()
        ),
    )
    return LoopAsyncClient(app)


class TestFeedUrlMerge:
    async def test_toggling_enabled_keeps_a_configured_feed_url(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        # The write is a full replace, so a payload carrying only
        # ``enabled`` has to be merged onto what is stored. Replacing
        # outright would silently reset the URL to the shipped default
        # every time somebody flipped the switch.
        label = _a_registered_label()
        async with _build_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
        ) as client:
            pointed = await client.put(
                f"{_BASE}/{label}",
                json={"enabled": True, "feed_url": _CUSTOM_URL},
                headers=_HEADERS,
            )
            assert pointed.status_code == 200

            toggled = await client.put(
                f"{_BASE}/{label}",
                json={"enabled": False},
                headers=_HEADERS,
            )
            assert toggled.status_code == 200
            by_label = {s["label"]: s for s in toggled.json()["data"]["sources"]}
            assert by_label[label]["enabled"] is False
            assert by_label[label]["feed_url"] == _CUSTOM_URL

    async def test_an_explicit_empty_feed_url_resets_to_the_shipped_default(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        # Omission and empty are deliberately different: this is the only
        # way back to the registry default, so it must not be merged away.
        label = _a_registered_label()
        async with _build_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
        ) as client:
            await client.put(
                f"{_BASE}/{label}",
                json={"enabled": True, "feed_url": _CUSTOM_URL},
                headers=_HEADERS,
            )
            reset = await client.put(
                f"{_BASE}/{label}",
                json={"enabled": True, "feed_url": ""},
                headers=_HEADERS,
            )
            assert reset.status_code == 200
            by_label = {s["label"]: s for s in reset.json()["data"]["sources"]}
            assert by_label[label]["feed_url"] != _CUSTOM_URL
