"""Tests for build_rollout_strategies dep-injection and build_rollback_executor."""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.factory import (
    build_rollback_executor,
    build_rollout_strategies,
)
from synthorg.meta.rollout.ab_test import ABTestRollout
from synthorg.meta.rollout.before_after import BeforeAfterRollout
from synthorg.meta.rollout.canary import CanarySubsetRollout
from synthorg.meta.rollout.inverse_dispatch import RollbackHandler
from synthorg.meta.rollout.rollback import RollbackExecutor
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit


class _Roster:
    async def list_agent_ids(self) -> tuple[NotBlankStr, ...]:
        return ()


class _ConfigMutator:
    async def set(self, *, path: str, value: object) -> None:
        pass


class _PromptMutator:
    async def restore_principle(self, *, scope: str, text: str) -> None:
        pass


class _ArchMutator:
    async def restore(self, *, target: str, previous_value: object) -> None:
        pass


class _CodeMutator:
    async def revert_file(self, *, path: str, content: str) -> None:
        pass


class TestBuildRolloutStrategies:
    def test_default_build_returns_all_three(self) -> None:
        strategies = build_rollout_strategies()
        assert set(strategies.keys()) == {"before_after", "canary", "ab_test"}
        assert isinstance(strategies["before_after"], BeforeAfterRollout)
        assert isinstance(strategies["canary"], CanarySubsetRollout)
        assert isinstance(strategies["ab_test"], ABTestRollout)

    def test_config_threaded_through(self) -> None:
        cfg = SelfImprovementConfig(enabled=True)
        strategies = build_rollout_strategies(cfg)
        assert "ab_test" in strategies

    def test_clock_injection(self) -> None:
        clock = FakeClock()
        strategies = build_rollout_strategies(
            clock=clock,
            roster=_Roster(),
        )
        # Access private to verify dep was wired into every strategy.
        assert strategies["before_after"]._clock is clock
        assert strategies["canary"]._clock is clock
        assert strategies["ab_test"]._clock is clock


class TestBuildRollbackExecutor:
    def test_registers_default_handlers(self) -> None:
        executor = build_rollback_executor(
            config_mutator=_ConfigMutator(),
            prompt_mutator=_PromptMutator(),
            architecture_mutator=_ArchMutator(),
            code_mutator=_CodeMutator(),
        )
        assert isinstance(executor, RollbackExecutor)
        handlers = executor._handlers
        assert set(handlers.keys()) == {
            NotBlankStr("revert_config"),
            NotBlankStr("restore_prompt"),
            NotBlankStr("revert_architecture"),
            NotBlankStr("revert_code"),
        }

    def test_extra_handlers_merge(self) -> None:
        from synthorg.meta.models import RollbackOperation

        class _MyHandler:
            async def revert(self, operation: RollbackOperation) -> int:
                _ = operation
                return 1

        extra: RollbackHandler = _MyHandler()
        executor = build_rollback_executor(
            config_mutator=_ConfigMutator(),
            prompt_mutator=_PromptMutator(),
            architecture_mutator=_ArchMutator(),
            code_mutator=_CodeMutator(),
            extra_handlers={"custom": extra},
        )
        handlers = executor._handlers
        assert NotBlankStr("custom") in handlers

    def test_extra_handlers_override_default(self) -> None:
        from synthorg.meta.models import RollbackOperation

        class _CustomConfigHandler:
            async def revert(self, operation: RollbackOperation) -> int:
                _ = operation
                return 42

        override: RollbackHandler = _CustomConfigHandler()
        executor = build_rollback_executor(
            config_mutator=_ConfigMutator(),
            prompt_mutator=_PromptMutator(),
            architecture_mutator=_ArchMutator(),
            code_mutator=_CodeMutator(),
            extra_handlers={"revert_config": override},
        )
        handlers = executor._handlers
        assert handlers[NotBlankStr("revert_config")] is override
