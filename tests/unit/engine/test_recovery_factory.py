"""Tests for build_recovery_strategy dispatch."""

import pytest

from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.engine.checkpoint.models import CheckpointConfig
from synthorg.engine.checkpoint.strategy import CheckpointRecoveryStrategy
from synthorg.engine.checkpoint.wiring import CheckpointWiring
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
from synthorg.workers.engine_assembly import _checkpoint_wiring_or_none
from tests._shared import make_app_state, mock_of


def _wiring(config: CheckpointConfig) -> CheckpointWiring:
    """Build a checkpoint wiring over repository doubles.

    Returns:
        The wiring.
    """
    return CheckpointWiring(
        checkpoint_repo=mock_of[CheckpointRepository](),
        heartbeat_repo=mock_of[HeartbeatRepository](),
        config=config,
    )


@pytest.mark.unit
class TestBuildRecoveryStrategy:
    """``build_recovery_strategy`` dispatches by config.strategy."""

    def test_fail_reassign_default(self) -> None:
        strategy = build_recovery_strategy(EngineRecoveryConfig(), checkpointing=None)
        assert isinstance(strategy, FailAndReassignStrategy)

    def test_explicit_fail_reassign(self) -> None:
        config = EngineRecoveryConfig(
            strategy=RecoveryStrategyType.FAIL_REASSIGN,
        )
        strategy = build_recovery_strategy(config, checkpointing=None)
        assert isinstance(strategy, FailAndReassignStrategy)

    def test_checkpoint_needs_wiring(self) -> None:
        config = EngineRecoveryConfig(strategy=RecoveryStrategyType.CHECKPOINT)

        with pytest.raises(RecoveryConfigError, match="checkpointing"):
            build_recovery_strategy(config, checkpointing=None)

    def test_checkpoint_returns_checkpoint_strategy(self) -> None:
        config = EngineRecoveryConfig(strategy=RecoveryStrategyType.CHECKPOINT)

        strategy = build_recovery_strategy(
            config, checkpointing=_wiring(CheckpointConfig())
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
            build_recovery_strategy(cfg.recovery, checkpointing=None),
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


def _from_app_state(app_state: AppState) -> RecoveryStrategy:
    """Build the strategy the way the assembly does: one wiring read, handed on.

    Returns:
        The strategy.
    """
    return build_recovery_strategy(
        app_state.config.recovery,
        checkpointing=_checkpoint_wiring_or_none(app_state),
    )


@pytest.mark.unit
class TestBuildRecoveryStrategyFromAppState:
    """The assembly's wiring read threads backend deps + checkpoint tuning."""

    def test_disconnected_backend_uses_fail_reassign_default(self) -> None:
        app_state = make_app_state(persistence=None)
        assert isinstance(_from_app_state(app_state), FailAndReassignStrategy)

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
            _from_app_state(app_state)

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

        strategy = _from_app_state(app_state)

        assert isinstance(strategy, CheckpointRecoveryStrategy)
        # The connected-backend branch must forward config.recovery.checkpoint,
        # not a fresh default, so operator tuning is honoured.
        assert strategy._config.persist_every_n_turns == 7
        assert strategy._config.max_resume_attempts == 5
        # ...and thread the active backend's repositories, so a repo-wiring
        # regression (e.g. constructing fresh repos) is caught.
        assert strategy._wiring.checkpoint_repo is backend.checkpoints
        assert strategy._wiring.heartbeat_repo is backend.heartbeats


@pytest.mark.unit
class TestRecoveryStrategyConformance:
    """Both built-in impls satisfy the @runtime_checkable Protocol."""

    def test_fail_reassign_satisfies_protocol(self) -> None:
        assert isinstance(FailAndReassignStrategy(), RecoveryStrategy)

    def test_checkpoint_satisfies_protocol(self) -> None:
        strategy = CheckpointRecoveryStrategy(wiring=_wiring(CheckpointConfig()))
        assert isinstance(strategy, RecoveryStrategy)
