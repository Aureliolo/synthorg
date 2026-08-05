"""Hot-reload coverage for the Chief-of-Staff chat model.

``chief_of_staff.chat_model`` is read live per call so an operator can
retarget the explain-chat model without a restart. The other per-feature
models (propose / routing / narrative) are covered in ``test_hot_models.py``.
"""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from synthorg.config.schema import RootConfig
from synthorg.meta.chief_of_staff.chat import ChiefOfStaffChat
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.models import ChatQuery
from synthorg.providers.protocol import CompletionProvider
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from tests._shared.model_binding import bound_ref, connections
from tests.unit.api.fakes import FakePersistenceBackend
from tests.unit.meta.test_service import _snap

pytestmark = pytest.mark.unit


@pytest.fixture
async def settings() -> AsyncIterator[SettingsService]:
    backend = FakePersistenceBackend()
    await backend.connect()
    yield SettingsService(repository=backend.settings, registry=get_registry())
    await backend.disconnect()


async def test_chat_model_read_live(settings: SettingsService) -> None:
    """A reassignment moves the dispatch to the newly named connection.

    The two connections get their own doubles. Sharing one would let a
    dispatch that ignores ``ModelRef.provider`` and always selects
    ``baked-conn`` satisfy every assertion, since the model id would still
    arrive on the only mock there was.
    """
    baked = AsyncMock(spec=CompletionProvider)
    baked.complete.return_value = SimpleNamespace(content="An answer.")
    live_provider = AsyncMock(spec=CompletionProvider)
    live_provider.complete.return_value = SimpleNamespace(content="An answer.")
    chat = ChiefOfStaffChat(
        connections=connections({"baked-conn": baked, "live-conn": live_provider}),
        config=ChiefOfStaffConfig(
            chat_model=bound_ref("baked-chat-001", provider="baked-conn")
        ),
        config_resolver=ConfigResolver(
            settings_service=settings, config=RootConfig(company_name="test")
        ),
    )
    query = ChatQuery(question="What changed?")

    await chat.ask(query, _snap())
    assert baked.complete.await_args.args[1] == "baked-chat-001"
    live_provider.complete.assert_not_awaited()

    live = serialize_model_ref(ModelRef(provider="live-conn", model_id="live-chat-001"))
    await settings.set("chief_of_staff", "chat_model", live)
    await chat.ask(query, _snap())
    assert live_provider.complete.await_args.args[1] == "live-chat-001"
    assert baked.complete.await_count == 1
