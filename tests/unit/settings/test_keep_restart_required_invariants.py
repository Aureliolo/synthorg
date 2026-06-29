"""Security invariant: the two acting/self-modifying switches stay restart-bound.

``chief_of_staff.direct_mcp_enabled`` (autonomous MCP acting, fail-closed at
construction) and ``self_improvement.code_modification_enabled`` (self-modifying
code, GitHub credentials validated only at startup) must remain
``restart_required=True`` and must not be watched by any meta settings
subscriber, so no live settings write can enable them. A careless future flip
fails this test.
"""

import pytest

from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from synthorg.settings.subscribers.chief_of_staff_alerts_subscriber import (
    ChiefOfStaffAlertsSettingsSubscriber,
)
from synthorg.settings.subscribers.meta_self_improvement_subscriber import (
    MetaSelfImprovementSettingsSubscriber,
)
from tests._shared import make_app_state
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.unit

_KEEP_KEYS: tuple[tuple[str, str], ...] = (
    ("chief_of_staff", "direct_mcp_enabled"),
    ("self_improvement", "code_modification_enabled"),
)


@pytest.mark.parametrize(("namespace", "key"), _KEEP_KEYS)
def test_keep_settings_remain_restart_required(namespace: str, key: str) -> None:
    defn = get_registry().get(namespace, key)
    assert defn is not None
    assert defn.restart_required is True


@pytest.mark.parametrize(("namespace", "key"), _KEEP_KEYS)
async def test_no_meta_subscriber_watches_keep_settings(
    namespace: str, key: str
) -> None:
    """No meta settings subscriber reacts to a KEEP-setting change.

    The dispatcher also skips restart-required settings outright, so a
    runtime write is never delivered; this guards the second line of defence
    (a subscriber must never opt one of these into live reconciliation).
    """
    backend = FakePersistenceBackend()
    await backend.connect()
    try:
        settings = SettingsService(repository=backend.settings, registry=get_registry())
        app_state = make_app_state()
        subscribers = (
            ChiefOfStaffAlertsSettingsSubscriber(
                app_state=app_state, settings_service=settings
            ),
            MetaSelfImprovementSettingsSubscriber(
                app_state=app_state, settings_service=settings
            ),
        )
        for sub in subscribers:
            assert (namespace, key) not in sub.watched_keys
    finally:
        await backend.disconnect()
