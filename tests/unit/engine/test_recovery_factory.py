"""Tests for build_recovery_strategy dispatch."""

import pytest

from synthorg.config.schema import RootConfig
from synthorg.engine.checkpoint.models import CheckpointConfig
from synthorg.engine.checkpoint.strategy import CheckpointRecoveryStrategy
from synthorg.engine.errors import RecoveryConfigError
from synthorg.engine.recovery import FailAndReassignStrategy, RecoveryStrategy
from synthorg.engine.recovery_config import (
    EngineRecoveryConfig,
    RecoveryStrategyType,
)
from synthorg.engine.recovery_factory import build_recovery_strategy
from synthorg.persistence.checkpoint_protocol import (
    CheckpointRepository,
    HeartbeatRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.workers._engine_assembly import _build_recovery_strategy
from tests._shared import make_app_state, mock_of


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
class TestRecoveryConfigWiring:
    """``EngineRecoveryConfig`` is reachable from the root config."""

    def test_root_config_defaults_to_fail_reassign(self) -> None:
        cfg = RootConfig(company_name="X")
        assert cfg.recovery.strategy is RecoveryStrategyType.FAIL_REASSIGN
        # The boot assembly feeds this straight into the factory.
        assert isinstance(
            build_recovery_strategy(cfg.recovery),
            FailAndReassignStrategy,
        )

    def test_root_config_carries_checkpoint_tuning(self) -> None:
        cfg = RootConfig(
            company_name="X",
            recovery=EngineRecoveryConfig(
                strategy=RecoveryStrategyType.CHECKPOINT,
                checkpoint=CheckpointConfig(
                    persist_every_n_turns=7,
                    max_resume_attempts=5,
                ),
            ),
        )
        assert cfg.recovery.checkpoint.persist_every_n_turns == 7
        assert cfg.recovery.checkpoint.max_resume_attempts == 5


@pytest.mark.unit
class TestBuildRecoveryStrategyFromAppState:
    """``_build_recovery_strategy`` threads backend deps + checkpoint tuning."""

    def test_disconnected_backend_uses_fail_reassign_default(self) -> None:
        app_state = make_app_state(persistence=None)
        assert isinstance(
            _build_recovery_strategy(app_state),
            FailAndReassignStrategy,
        )

    def test_checkpoint_strategy_without_backend_fails_fast(self) -> None:
        # Selecting CHECKPOINT without a connected persistence backend must
        # raise at boot, not silently fall back to fail-reassign.
        app_state = make_app_state(
            config=RootConfig(
                company_name="X",
                recovery=EngineRecoveryConfig(
                    strategy=RecoveryStrategyType.CHECKPOINT,
                ),
            ),
            persistence=None,
        )
        with pytest.raises(RecoveryConfigError):
            _build_recovery_strategy(app_state)

    def test_connected_backend_threads_config_checkpoint_tuning(self) -> None:
        backend = mock_of[PersistenceBackend](
            is_connected=True,
            checkpoints=mock_of[CheckpointRepository](),
            heartbeats=mock_of[HeartbeatRepository](),
        )
        app_state = make_app_state(
            config=RootConfig(
                company_name="X",
                recovery=EngineRecoveryConfig(
                    strategy=RecoveryStrategyType.CHECKPOINT,
                    checkpoint=CheckpointConfig(
                        persist_every_n_turns=7,
                        max_resume_attempts=5,
                    ),
                ),
            ),
            persistence=backend,
        )

        strategy = _build_recovery_strategy(app_state)

        assert isinstance(strategy, CheckpointRecoveryStrategy)
        # The connected-backend branch must forward config.recovery.checkpoint,
        # not a fresh default, so operator tuning is honoured.
        assert strategy._config.persist_every_n_turns == 7
        assert strategy._config.max_resume_attempts == 5
        # ...and thread the active backend's repositories, so a repo-wiring
        # regression (e.g. constructing fresh repos) is caught.
        assert strategy._checkpoint_repo is backend.checkpoints
        assert strategy._heartbeat_repo is backend.heartbeats


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
