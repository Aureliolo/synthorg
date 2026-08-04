"""Hot-reload coverage for the code-modification model identifier.

The code-modification capability stays restart-bound, but its model
(``self_improvement.code_modification_model``) is read live per generation
so an operator can retarget it without a restart.
"""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from synthorg.config.schema import RootConfig
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.meta.config import CodeModificationConfig, SelfImprovementConfig
from synthorg.meta.strategies.code_modification import CodeModificationStrategy
from synthorg.meta.validation.scope_validator import ScopeValidator
from synthorg.providers.base import BaseCompletionProvider
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from tests._shared.model_binding import connections
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.unit


@pytest.fixture
async def settings() -> AsyncIterator[SettingsService]:
    backend = FakePersistenceBackend()
    await backend.connect()
    yield SettingsService(repository=backend.settings, registry=get_registry())
    await backend.disconnect()


def _strategy(
    settings: SettingsService, *, provider: AsyncMock
) -> CodeModificationStrategy:
    cfg = SelfImprovementConfig(
        enabled=True,
        code_modification_enabled=True,
        code_modification=CodeModificationConfig(
            github_token="t",
            github_repo="owner/repo",
        ),
    )
    return CodeModificationStrategy(
        config=cfg,
        connections=connections({"first-conn": provider, "second-conn": provider}),
        scope_validator=ScopeValidator(allowed_paths=(), forbidden_paths=()),
        config_resolver=ConfigResolver(
            settings_service=settings, config=RootConfig(company_name="test")
        ),
    )


async def test_code_modification_refuses_without_a_configured_pair(
    settings: SettingsService,
) -> None:
    """No pair means no generation: there is no connection to borrow."""
    provider = AsyncMock(spec=BaseCompletionProvider)
    strategy = _strategy(settings, provider=provider)

    with pytest.raises(ServiceUnavailableError, match="code_modification_model"):
        await strategy._call_llm("prompt")
    provider.complete.assert_not_called()


async def test_code_modification_model_read_live(settings: SettingsService) -> None:
    """The provider call retargets to the live pair, both halves of it."""
    provider = AsyncMock(spec=BaseCompletionProvider)
    provider.complete.return_value = SimpleNamespace(content="[]")
    strategy = _strategy(settings, provider=provider)

    await settings.set(
        "self_improvement",
        "code_modification_model",
        serialize_model_ref(ModelRef(provider="first-conn", model_id="first-codemod")),
    )
    await strategy._call_llm("prompt")
    assert provider.complete.await_args.kwargs["model"] == "first-codemod"

    await settings.set(
        "self_improvement",
        "code_modification_model",
        serialize_model_ref(ModelRef(provider="second-conn", model_id="live-codemod")),
    )
    await strategy._call_llm("prompt")
    assert provider.complete.await_args.kwargs["model"] == "live-codemod"
