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
from synthorg.meta.config import CodeModificationConfig, SelfImprovementConfig
from synthorg.meta.strategies.code_modification import CodeModificationStrategy
from synthorg.meta.validation.scope_validator import ScopeValidator
from synthorg.providers.base import BaseCompletionProvider
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
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
            llm_model="baked-codemod-001",
        ),
    )
    return CodeModificationStrategy(
        config=cfg,
        provider=provider,
        scope_validator=ScopeValidator(allowed_paths=(), forbidden_paths=()),
        config_resolver=ConfigResolver(
            settings_service=settings, config=RootConfig(company_name="test")
        ),
    )


async def test_code_modification_model_read_live(settings: SettingsService) -> None:
    """The provider call uses the live model, falling back to the baked one."""
    provider = AsyncMock(spec=BaseCompletionProvider)
    provider.complete.return_value = SimpleNamespace(content="[]")
    strategy = _strategy(settings, provider=provider)

    await strategy._call_llm("prompt")
    assert provider.complete.await_args.kwargs["model"] == "baked-codemod-001"

    await settings.set("self_improvement", "code_modification_model", "live-codemod")
    await strategy._call_llm("prompt")
    assert provider.complete.await_args.kwargs["model"] == "live-codemod"
