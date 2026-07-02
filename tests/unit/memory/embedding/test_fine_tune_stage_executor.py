"""StageExecutor seam: protocol conformance and dispatch delegation."""

from unittest.mock import AsyncMock, patch

import pytest

from synthorg.memory.embedding.fine_tune import FineTuneStage
from synthorg.memory.embedding.fine_tune_models import FineTuneExecutionConfig
from synthorg.memory.embedding.fine_tune_run_helpers import (
    resolve_execution_config,
)
from synthorg.memory.embedding.fine_tune_stage_executor import (
    InProcessStageExecutor,
    StageExecutor,
)

pytestmark = pytest.mark.unit


class TestInProcessStageExecutor:
    def test_satisfies_protocol(self) -> None:
        assert isinstance(InProcessStageExecutor(), StageExecutor)

    async def test_delegates_to_shared_dispatch(self) -> None:
        mock_dispatch = AsyncMock()
        config: dict[str, object] = {"stage": "training"}
        with patch(
            "synthorg.memory.embedding.fine_tune_stage_executor.dispatch_stage",
            mock_dispatch,
        ):
            await InProcessStageExecutor().run_stage(
                stage=FineTuneStage.TRAINING,
                config=config,
                progress_callback=None,
                cancellation=None,
            )
        mock_dispatch.assert_awaited_once_with(
            FineTuneStage.TRAINING,
            config,
            None,
            progress_callback=None,
        )


class TestResolveExecutionConfig:
    def test_explicit_request_wins(self) -> None:
        requested = FineTuneExecutionConfig(backend="in-process")
        resolved = resolve_execution_config(
            requested,
            fine_tune_image="example.test/fine-tune:1",
            default_gpu=True,
            default_memory_limit="16g",
            default_timeout_seconds=100.0,
        )
        assert resolved is requested

    def test_image_configured_derives_docker(self) -> None:
        resolved = resolve_execution_config(
            None,
            fine_tune_image="example.test/fine-tune:1",
            default_gpu=True,
            default_memory_limit="16g",
            default_timeout_seconds=100.0,
        )
        assert resolved == FineTuneExecutionConfig(
            backend="docker",
            image="example.test/fine-tune:1",
            gpu_enabled=True,
            memory_limit="16g",
            timeout_seconds=100.0,
        )

    def test_no_image_derives_in_process(self) -> None:
        resolved = resolve_execution_config(
            None,
            fine_tune_image="",
            default_gpu=True,
            default_memory_limit="16g",
            default_timeout_seconds=100.0,
        )
        assert resolved.backend == "in-process"
        assert resolved.image is None
        assert resolved.timeout_seconds == 100.0
