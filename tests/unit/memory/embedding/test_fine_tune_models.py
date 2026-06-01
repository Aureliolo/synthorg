"""Tests for fine-tuning API models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.memory.embedding.cancellation import CancellationToken
from synthorg.memory.embedding.fine_tune import FineTuneStage
from synthorg.memory.embedding.fine_tune_models import (
    CheckpointRecord,
    EvalMetrics,
    FineTuneExecutionConfig,
    FineTuneRequest,
    FineTuneRun,
    FineTuneRunConfig,
    FineTuneStatus,
    PreflightCheck,
    PreflightResult,
)
from synthorg.memory.errors import FineTuneCancelledError

# -- CancellationToken ------------------------------------------------


@pytest.mark.unit
class TestCancellationToken:
    def test_not_cancelled_initially(self) -> None:
        token = CancellationToken()
        assert token.is_cancelled is False

    def test_cancel_sets_flag(self) -> None:
        token = CancellationToken()
        token.cancel()
        assert token.is_cancelled is True

    def test_check_passes_when_not_cancelled(self) -> None:
        token = CancellationToken()
        token.check()  # should not raise

    def test_check_raises_when_cancelled(self) -> None:
        token = CancellationToken()
        token.cancel()
        with pytest.raises(FineTuneCancelledError, match="cancelled"):
            token.check()

    def test_cancel_is_idempotent(self) -> None:
        token = CancellationToken()
        token.cancel()
        token.cancel()
        assert token.is_cancelled is True


# -- EvalMetrics ------------------------------------------------------


@pytest.mark.unit
class TestEvalMetrics:
    def test_basic(self) -> None:
        m = EvalMetrics(
            ndcg_at_10=0.6,
            recall_at_10=0.7,
            base_ndcg_at_10=0.5,
            base_recall_at_10=0.6,
        )
        assert m.ndcg_at_10 == 0.6
        assert m.base_ndcg_at_10 == 0.5

    def test_improvement_computed(self) -> None:
        m = EvalMetrics(
            ndcg_at_10=0.6,
            recall_at_10=0.7,
            base_ndcg_at_10=0.5,
            base_recall_at_10=0.5,
        )
        assert abs(m.improvement_ndcg - 0.2) < 1e-10
        assert abs(m.improvement_recall - 0.4) < 1e-10

    def test_improvement_zero_base(self) -> None:
        m = EvalMetrics(
            ndcg_at_10=0.5,
            recall_at_10=0.5,
            base_ndcg_at_10=0.0,
            base_recall_at_10=0.0,
        )
        assert m.improvement_ndcg == 0.0
        assert m.improvement_recall == 0.0

    def test_frozen(self) -> None:
        m = EvalMetrics(
            ndcg_at_10=0.6,
            recall_at_10=0.7,
            base_ndcg_at_10=0.5,
            base_recall_at_10=0.6,
        )
        with pytest.raises(ValidationError):
            m.ndcg_at_10 = 0.9  # type: ignore[misc]

    def test_negative_score_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvalMetrics(
                ndcg_at_10=-0.1,
                recall_at_10=0.7,
                base_ndcg_at_10=0.5,
                base_recall_at_10=0.6,
            )

    def test_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvalMetrics(
                ndcg_at_10=1.1,
                recall_at_10=0.7,
                base_ndcg_at_10=0.5,
                base_recall_at_10=0.6,
            )

    def test_json_roundtrip(self) -> None:
        m = EvalMetrics(
            ndcg_at_10=0.6,
            recall_at_10=0.7,
            base_ndcg_at_10=0.5,
            base_recall_at_10=0.6,
        )
        restored = EvalMetrics.model_validate_json(m.model_dump_json())
        assert restored.ndcg_at_10 == m.ndcg_at_10
        assert restored.improvement_ndcg == m.improvement_ndcg


# -- FineTuneRunConfig ------------------------------------------------


@pytest.mark.unit
class TestFineTuneRunConfig:
    def test_defaults(self) -> None:
        cfg = FineTuneRunConfig(
            source_dir="/docs",
            base_model="test-model",
            output_dir="/out",
        )
        assert cfg.epochs == 3
        assert cfg.learning_rate == 1e-5
        assert cfg.temperature == 0.02
        assert cfg.top_k == 4
        assert cfg.batch_size == 128
        assert cfg.validation_split == 0.1

    def test_custom_values(self) -> None:
        cfg = FineTuneRunConfig(
            source_dir="/docs",
            base_model="test-model",
            output_dir="/out",
            epochs=5,
            batch_size=64,
            validation_split=0.2,
        )
        assert cfg.epochs == 5
        assert cfg.batch_size == 64
        assert cfg.validation_split == 0.2

    def test_frozen(self) -> None:
        cfg = FineTuneRunConfig(
            source_dir="/docs",
            base_model="test-model",
            output_dir="/out",
        )
        with pytest.raises(ValidationError):
            cfg.epochs = 10  # type: ignore[misc]


# -- FineTuneRun ------------------------------------------------------


@pytest.mark.unit
class TestFineTuneRun:
    def _make_run(self, **overrides: object) -> FineTuneRun:
        now = datetime.now(tz=UTC)
        defaults: dict[str, object] = {
            "id": "run-1",
            "stage": FineTuneStage.GENERATING_DATA,
            "config": FineTuneRunConfig(
                source_dir="/docs",
                base_model="test-model",
                output_dir="/out",
            ),
            "started_at": now,
            "updated_at": now,
        }
        defaults.update(overrides)
        return FineTuneRun(**defaults)  # type: ignore[arg-type]

    def test_basic(self) -> None:
        run = self._make_run()
        assert run.id == "run-1"
        assert run.stage == FineTuneStage.GENERATING_DATA
        assert run.duration_seconds is None

    def test_duration_computed(self) -> None:
        now = datetime.now(tz=UTC)
        run = self._make_run(
            stage=FineTuneStage.COMPLETE,
            started_at=now,
            completed_at=now,
        )
        assert run.duration_seconds == 0.0

    def test_stages_completed_default_empty(self) -> None:
        run = self._make_run()
        assert run.stages_completed == ()


# -- CheckpointRecord -------------------------------------------------


@pytest.mark.unit
class TestCheckpointRecord:
    def test_basic(self) -> None:
        now = datetime.now(tz=UTC)
        cp = CheckpointRecord(
            id="cp-1",
            run_id="run-1",
            model_path="/models/cp-1",
            base_model="test-model",
            doc_count=100,
            size_bytes=1024,
            created_at=now,
        )
        assert cp.is_active is False
        assert cp.eval_metrics is None
        assert cp.backup_config_json is None

    def test_with_eval_metrics(self) -> None:
        now = datetime.now(tz=UTC)
        cp = CheckpointRecord(
            id="cp-1",
            run_id="run-1",
            model_path="/models/cp-1",
            base_model="test-model",
            doc_count=100,
            eval_metrics=EvalMetrics(
                ndcg_at_10=0.6,
                recall_at_10=0.7,
                base_ndcg_at_10=0.5,
                base_recall_at_10=0.6,
            ),
            size_bytes=1024,
            created_at=now,
        )
        assert cp.eval_metrics is not None
        assert cp.eval_metrics.ndcg_at_10 == 0.6


# -- PreflightCheck/Result --------------------------------------------


@pytest.mark.unit
class TestPreflightCheck:
    @pytest.mark.parametrize("status", ["pass", "warn", "fail"])
    def test_valid_statuses(self, status: str) -> None:
        check = PreflightCheck(
            name="deps",
            status=status,  # type: ignore[arg-type]
            message="OK",
        )
        assert check.status == status

    def test_invalid_status_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PreflightCheck(
                name="deps",
                status="invalid",  # type: ignore[arg-type]
                message="bad",
            )


@pytest.mark.unit
class TestPreflightResult:
    def test_can_proceed_all_pass(self) -> None:
        result = PreflightResult(
            checks=(
                PreflightCheck(name="a", status="pass", message="OK"),
                PreflightCheck(name="b", status="warn", message="low"),
            ),
        )
        assert result.can_proceed is True

    def test_can_proceed_false_on_fail(self) -> None:
        result = PreflightResult(
            checks=(
                PreflightCheck(name="a", status="pass", message="OK"),
                PreflightCheck(name="b", status="fail", message="missing"),
            ),
        )
        assert result.can_proceed is False

    def test_empty_checks_can_proceed(self) -> None:
        result = PreflightResult()
        assert result.can_proceed is True

    def test_recommended_batch_size(self) -> None:
        result = PreflightResult(recommended_batch_size=64)
        assert result.recommended_batch_size == 64


# -- FineTuneStatus extensions ----------------------------------------


@pytest.mark.unit
class TestFineTuneStatusExtensions:
    def test_run_id_default_none(self) -> None:
        status = FineTuneStatus()
        assert status.run_id is None

    def test_run_id_set(self) -> None:
        status = FineTuneStatus(
            run_id="run-1",
            stage=FineTuneStage.TRAINING,
            progress=0.5,
        )
        assert status.run_id == "run-1"

    def test_evaluating_is_active_stage(self) -> None:
        status = FineTuneStatus(
            stage=FineTuneStage.EVALUATING,
            progress=0.3,
        )
        assert status.stage == FineTuneStage.EVALUATING

    def test_evaluating_rejects_error(self) -> None:
        with pytest.raises(ValidationError, match="error must be None"):
            FineTuneStatus(
                stage=FineTuneStage.EVALUATING,
                progress=0.3,
                error="should not be here",
            )


# -- FineTuneRequest extensions ---------------------------------------


@pytest.mark.unit
class TestFineTuneRequestExtensions:
    def test_override_defaults_none(self) -> None:
        req = FineTuneRequest(source_dir="/docs")
        assert req.epochs is None
        assert req.learning_rate is None
        assert req.temperature is None
        assert req.top_k is None
        assert req.batch_size is None
        assert req.validation_split is None
        assert req.resume_run_id is None

    def test_override_values(self) -> None:
        req = FineTuneRequest(
            source_dir="/docs",
            epochs=5,
            learning_rate=2e-5,
            temperature=0.05,
            top_k=8,
            batch_size=64,
            validation_split=0.2,
        )
        assert req.epochs == 5
        assert req.batch_size == 64

    def test_resume_run_id(self) -> None:
        req = FineTuneRequest(
            source_dir="/docs",
            resume_run_id="run-old-1",
        )
        assert req.resume_run_id == "run-old-1"

    def test_invalid_epochs_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FineTuneRequest(source_dir="/docs", epochs=0)

    def test_invalid_validation_split_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FineTuneRequest(source_dir="/docs", validation_split=1.0)


@pytest.mark.unit
class TestBaseModelValidation:
    """``base_model`` RCE / SSRF defence on both request and run config."""

    @pytest.mark.parametrize(
        "bad_model",
        [
            "https://evil.example/model",
            "file://etc/passwd",
            "../../etc/passwd",
            "models\\windows",
            "C:/models/x",
        ],
    )
    def test_request_rejects_unsafe_base_model(self, bad_model: str) -> None:
        with pytest.raises(ValidationError, match="base_model"):
            FineTuneRequest(source_dir="/docs", base_model=bad_model)

    @pytest.mark.parametrize(
        "bad_model",
        [
            "https://evil.example/model",
            "../escape",
            "back\\slash",
            "D:/drive",
        ],
    )
    def test_run_config_rejects_unsafe_base_model(self, bad_model: str) -> None:
        with pytest.raises(ValidationError, match="base_model"):
            FineTuneRunConfig(
                source_dir="/docs",
                base_model=bad_model,
                output_dir="/out",
            )

    @pytest.mark.parametrize(
        "good_model",
        ["all-MiniLM-L6-v2", "example-org/example-encoder", "/data/models/local"],
    )
    def test_accepts_safe_base_model(self, good_model: str) -> None:
        cfg = FineTuneRunConfig(
            source_dir="/docs",
            base_model=good_model,
            output_dir="/out",
        )
        assert cfg.base_model == good_model

    def test_request_allows_none_base_model(self) -> None:
        req = FineTuneRequest(source_dir="/docs")
        assert req.base_model is None


@pytest.mark.unit
class TestFineTuneExecutionConfig:
    """FineTuneExecutionConfig model validation."""

    def test_defaults(self) -> None:
        config = FineTuneExecutionConfig()
        assert config.backend == "in-process"
        assert config.image is None
        assert config.gpu_enabled is False
        assert config.memory_limit == "8g"
        assert config.timeout_seconds == 7200.0

    def test_frozen(self) -> None:
        config = FineTuneExecutionConfig()
        with pytest.raises(ValidationError):
            config.backend = "docker"  # type: ignore[misc]

    def test_docker_backend_requires_image(self) -> None:
        with pytest.raises(ValidationError, match="image is required"):
            FineTuneExecutionConfig(backend="docker")

    def test_docker_backend_with_image(self) -> None:
        config = FineTuneExecutionConfig(
            backend="docker",
            image="ghcr.io/aureliolo/synthorg-fine-tune:latest",
        )
        assert config.backend == "docker"
        assert config.image is not None

    def test_in_process_ignores_image(self) -> None:
        config = FineTuneExecutionConfig(
            backend="in-process",
            image="some-image:tag",
        )
        assert config.backend == "in-process"

    def test_blank_memory_limit_rejected(self) -> None:
        with pytest.raises(ValidationError, match="memory_limit"):
            FineTuneExecutionConfig(memory_limit="")

    def test_whitespace_memory_limit_rejected(self) -> None:
        with pytest.raises(ValidationError, match="memory_limit"):
            FineTuneExecutionConfig(memory_limit="   ")

    def test_nan_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FineTuneExecutionConfig(timeout_seconds=float("nan"))

    def test_zero_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FineTuneExecutionConfig(timeout_seconds=0.0)

    def test_gpu_enabled(self) -> None:
        config = FineTuneExecutionConfig(
            backend="docker",
            image="test:latest",
            gpu_enabled=True,
        )
        assert config.gpu_enabled is True

    def test_invalid_backend_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FineTuneExecutionConfig(backend="kubernetes")  # type: ignore[arg-type]
