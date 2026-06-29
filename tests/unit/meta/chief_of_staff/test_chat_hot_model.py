"""Hot-reload coverage for the Chief-of-Staff chat model.

``chief_of_staff.chat_model`` is read live per call so an operator can
retarget the explain-chat model without a restart. The other per-feature
models (propose / routing / narrative) use the identical resolver seam.
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
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
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
    provider = AsyncMock(spec=CompletionProvider)
    provider.complete.return_value = SimpleNamespace(content="An answer.")
    chat = ChiefOfStaffChat(
        provider=provider,
        config=ChiefOfStaffConfig(chat_model="baked-chat-001"),
        config_resolver=ConfigResolver(
            settings_service=settings, config=RootConfig(company_name="test")
        ),
    )
    query = ChatQuery(question="What changed?")

    await chat.ask(query, _snap())
    assert provider.complete.await_args.args[1] == "baked-chat-001"

    await settings.set("chief_of_staff", "chat_model", "live-chat-001")
    await chat.ask(query, _snap())
    assert provider.complete.await_args.args[1] == "live-chat-001"
