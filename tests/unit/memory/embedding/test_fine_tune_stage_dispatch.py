"""Shared stage-dispatch contract: builders and dispatch coercion."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.memory.embedding.fine_tune import FineTuneStage
from synthorg.memory.embedding.fine_tune_models import FineTuneRunConfig
from synthorg.memory.embedding.fine_tune_stage_dispatch import (
    CONTAINER_STAGES,
    dispatch_stage,
    evaluating_stage_config,
    mining_stage_config,
    training_stage_config,
)

pytestmark = pytest.mark.unit


def _cfg() -> FineTuneRunConfig:
    return FineTuneRunConfig(
        source_dir="/docs",
        base_model="test-model",
        output_dir="/out",
        epochs=5,
        learning_rate=2e-5,
        temperature=0.05,
        top_k=8,
        batch_size=64,
    )


class TestContainerStages:
    def test_only_torch_bound_stages(self) -> None:
        assert {
            FineTuneStage.MINING_NEGATIVES,
            FineTuneStage.TRAINING,
            FineTuneStage.EVALUATING,
        } == CONTAINER_STAGES


class TestConfigBuilders:
    def test_mining_config(self) -> None:
        config = mining_stage_config(
            _cfg(), out_dir="/out/runs/r1", train_path="/out/runs/r1/training.jsonl"
        )
        assert config == {
            "stage": "mining_negatives",
            "training_data_path": "/out/runs/r1/training.jsonl",
            "base_model": "test-model",
            "output_dir": "/out/runs/r1",
            "top_k": 8,
        }

    def test_training_config(self) -> None:
        config = training_stage_config(
            _cfg(),
            out_dir="/out/runs/r1",
            triples_path="/out/runs/r1/training_triples.jsonl",
        )
        assert config == {
            "stage": "training",
            "training_data_path": "/out/runs/r1/training_triples.jsonl",
            "base_model": "test-model",
            "output_dir": "/out/runs/r1",
            "epochs": 5,
            "learning_rate": 2e-5,
            "temperature": 0.05,
            "batch_size": 64,
        }

    def test_evaluating_config(self) -> None:
        config = evaluating_stage_config(
            _cfg(),
            out_dir="/out/runs/r1",
            checkpoint_path="/out/runs/r1/checkpoint",
            val_path="/out/runs/r1/validation.jsonl",
        )
        assert config == {
            "stage": "evaluating",
            "checkpoint_path": "/out/runs/r1/checkpoint",
            "base_model": "test-model",
            "validation_data_path": "/out/runs/r1/validation.jsonl",
            "output_dir": "/out/runs/r1",
        }


class TestDispatchStage:
    async def test_mining_dispatch_coerces_kwargs(self) -> None:
        config = mining_stage_config(
            _cfg(), out_dir="/out/runs/r1", train_path="/t.jsonl"
        )
        # The dict crosses the container boundary as JSON; dispatch must
        # coerce what comes back out.
        config = json.loads(json.dumps(config))
        mock_fn = AsyncMock()
        with patch("synthorg.memory.embedding.fine_tune.mine_hard_negatives", mock_fn):
            await dispatch_stage(FineTuneStage.MINING_NEGATIVES, config, None)
        mock_fn.assert_awaited_once_with(
            training_data_path="/t.jsonl",
            base_model="test-model",
            output_dir="/out/runs/r1",
            top_k=8,
            progress_callback=None,
            cancellation=None,
        )

    async def test_training_dispatch_coerces_kwargs(self) -> None:
        config = json.loads(
            json.dumps(
                training_stage_config(
                    _cfg(), out_dir="/out/runs/r1", triples_path="/triples.jsonl"
                )
            )
        )
        mock_fn = AsyncMock()
        with patch(
            "synthorg.memory.embedding.fine_tune.contrastive_fine_tune", mock_fn
        ):
            await dispatch_stage(FineTuneStage.TRAINING, config, None)
        mock_fn.assert_awaited_once_with(
            training_data_path="/triples.jsonl",
            base_model="test-model",
            output_dir="/out/runs/r1",
            epochs=5,
            learning_rate=2e-5,
            temperature=0.05,
            batch_size=64,
            progress_callback=None,
            cancellation=None,
        )

    async def test_evaluating_dispatch(self) -> None:
        config = evaluating_stage_config(
            _cfg(),
            out_dir="/out/runs/r1",
            checkpoint_path="/ckpt",
            val_path="/val.jsonl",
        )
        mock_fn = AsyncMock()
        with patch("synthorg.memory.embedding.fine_tune.evaluate_checkpoint", mock_fn):
            await dispatch_stage(FineTuneStage.EVALUATING, config, None)
        mock_fn.assert_awaited_once_with(
            checkpoint_path="/ckpt",
            base_model="test-model",
            validation_data_path="/val.jsonl",
            output_dir="/out/runs/r1",
            progress_callback=None,
            cancellation=None,
        )

    @pytest.mark.parametrize(
        "stage",
        [FineTuneStage.GENERATING_DATA, FineTuneStage.DEPLOYING, FineTuneStage.IDLE],
    )
    async def test_non_container_stage_rejected(self, stage: FineTuneStage) -> None:
        with pytest.raises(ValueError, match="not container-dispatchable"):
            await dispatch_stage(stage, {}, None)
