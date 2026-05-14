"""Tests for build_recovery_strategy dispatch."""

import pytest

from synthorg.engine.checkpoint.models import CheckpointConfig
from synthorg.engine.checkpoint.strategy import CheckpointRecoveryStrategy
from synthorg.engine.errors import RecoveryConfigError
from synthorg.engine.recovery import FailAndReassignStrategy, RecoveryStrategy
from synthorg.engine.recovery_config import (
    EngineRecoveryConfig,
    RecoveryStrategyType,
)
from synthorg.engine.recovery_factory import build_recovery_strategy
from synthorg.persistence.checkpoint_protocol import CheckpointRepository
from tests._shared import mock_of


@pytest.mark.unit
class TestBuildRecoveryStrategy:
    """``build_recovery_strategy`` dispatches by config.strategy."""

    def test_fail_reassign_default(self) -> None:
        strategy = build_recovery_strategy(EngineRecoveryConfig())
        assert isinstance(strategy, FailAndReassignStrategy)

    def test_explicit_fail_reassign(self) -> None:
        config = EngineRecoveryConfig(
            strategy=RecoveryStrategyType.FAIL_REASSIGN,
        )
        strategy = build_recovery_strategy(config)
        assert isinstance(strategy, FailAndReassignStrategy)

    def test_checkpoint_needs_repo_and_config(self) -> None:
        config = EngineRecoveryConfig(strategy=RecoveryStrategyType.CHECKPOINT)

        with pytest.raises(RecoveryConfigError, match="checkpoint_repo"):
            build_recovery_strategy(config)

        with pytest.raises(RecoveryConfigError, match="checkpoint_config"):
            build_recovery_strategy(
                config,
                checkpoint_repo=mock_of[CheckpointRepository](),
            )

    def test_checkpoint_returns_checkpoint_strategy(self) -> None:
        config = EngineRecoveryConfig(strategy=RecoveryStrategyType.CHECKPOINT)

        strategy = build_recovery_strategy(
            config,
            checkpoint_repo=mock_of[CheckpointRepository](),
            checkpoint_config=CheckpointConfig(),
        )
        assert isinstance(strategy, CheckpointRecoveryStrategy)


@pytest.mark.unit
class TestRecoveryStrategyConformance:
    """Both built-in impls satisfy the @runtime_checkable Protocol."""

    def test_fail_reassign_satisfies_protocol(self) -> None:
        assert isinstance(FailAndReassignStrategy(), RecoveryStrategy)

    def test_checkpoint_satisfies_protocol(self) -> None:
        strategy = CheckpointRecoveryStrategy(
            checkpoint_repo=mock_of[CheckpointRepository](),
            config=CheckpointConfig(),
        )
        assert isinstance(strategy, RecoveryStrategy)
