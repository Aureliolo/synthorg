# module-kind: tests
"""The session-scoped fakes reach every test empty, not just client tests.

``fake_persistence`` and ``fake_message_bus`` are built once per worker and
handed to every API test. The reset that empties them used to hang off the
client fixtures, so a test that took the backend and built its own app read
whatever the previous test had written. Which test that is is not stable:
``pytest-split`` partitions the suite by test rather than by file, so a shard
boundary decides the neighbour, and a roster a setup-template case wrote became
the first agent a YAML-roster case read.

The two cases below run in this order in the same file, which is what makes the
inheritance observable: the first writes into both fakes without asking for a
client, and the second asserts it inherited neither.
"""

import pytest

from synthorg.communication.channel import Channel
from synthorg.core.types import NotBlankStr
from synthorg.persistence.settings_protocol import SettingRow
from tests.unit.api.conftest import FakeMessageBus, FakePersistenceBackend

pytestmark = pytest.mark.unit

_NAMESPACE = "company"
_KEY = "agents"
_CHANNEL = "isolation-probe"


async def test_a_test_can_write_into_the_shared_fakes(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> None:
    # Deliberately no client fixture: its absence is what used to mean nothing
    # cleaned up after this.
    await fake_persistence.settings.save(
        SettingRow(
            namespace=NotBlankStr(_NAMESPACE),
            key=NotBlankStr(_KEY),
            value='[{"name": "left-behind", "role": "dev", "department": "eng"}]',
            updated_at="2026-01-01T00:00:00+00:00",
        )
    )
    await fake_message_bus.create_channel(Channel(name=NotBlankStr(_CHANNEL)))

    assert await fake_persistence.settings.get((_NAMESPACE, _KEY)) is not None
    assert [ch.name for ch in await fake_message_bus.list_channels()] == [_CHANNEL]


async def test_the_next_test_inherits_none_of_it(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> None:
    assert await fake_persistence.settings.get((_NAMESPACE, _KEY)) is None
    assert await fake_message_bus.list_channels() == ()
