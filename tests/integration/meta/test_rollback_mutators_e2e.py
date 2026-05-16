"""End-to-end integration: rollback executor assembled from real mutators.

Exercises the assembly path: the four concrete mutators plug into
``build_rollback_executor`` and produce the expected dispatch table.
Per-mutator behaviour is covered by unit tests under
``tests/unit/meta/rollout/mutators/`` and by the dual-backend
conformance test under
``tests/conformance/persistence/test_principle_override_repository.py``.
"""

from pathlib import Path
from types import MappingProxyType
from typing import cast
from unittest.mock import AsyncMock, create_autospec

import pytest

from synthorg.meta.factory import build_rollback_executor
from synthorg.meta.rollout.mutators import (
    PrincipleOverridePromptMutator,
    RoutedArchitectureMutator,
    SettingsServiceConfigMutator,
    WorkspaceCodeMutator,
)
from synthorg.persistence.principle_override_protocol import (
    PrincipleOverrideRepository,
)
from synthorg.settings.service import SettingsService

pytestmark = pytest.mark.integration


@pytest.fixture
def settings_service() -> SettingsService:
    service = create_autospec(SettingsService, instance=True, spec_set=True)
    service.save = AsyncMock()
    return cast("SettingsService", service)


@pytest.fixture
def override_repo() -> PrincipleOverrideRepository:
    repo = create_autospec(
        PrincipleOverrideRepository,
        instance=True,
        spec_set=True,
    )
    repo.save = AsyncMock()
    return cast("PrincipleOverrideRepository", repo)


class TestRollbackExecutorAssembly:
    def test_builds_executor_with_all_four_mutators(
        self,
        settings_service: SettingsService,
        override_repo: PrincipleOverrideRepository,
        tmp_path: Path,
    ) -> None:
        """build_rollback_executor accepts all four mutator types."""
        config_mutator = SettingsServiceConfigMutator(
            settings_service=settings_service,
        )
        prompt_mutator = PrincipleOverridePromptMutator(
            override_repo=override_repo,
        )
        architecture_mutator = RoutedArchitectureMutator({"role": AsyncMock()})
        code_mutator = WorkspaceCodeMutator(workspace_root=tmp_path)

        executor = build_rollback_executor(
            config_mutator=config_mutator,
            prompt_mutator=prompt_mutator,
            architecture_mutator=architecture_mutator,
            code_mutator=code_mutator,
        )

        # The executor exposes a read-only handler mapping with the
        # four built-in operation_type keys.
        handlers = executor._handlers
        assert isinstance(handlers, MappingProxyType)
        assert set(handlers.keys()) == {
            "revert_config",
            "restore_prompt",
            "revert_architecture",
            "revert_code",
        }
