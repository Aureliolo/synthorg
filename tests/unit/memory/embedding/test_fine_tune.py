"""Tests for fine-tuning pipeline stage functions."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, TypedDict, cast
from unittest.mock import patch

import numpy as np
import numpy.typing as npt
import pytest

from synthorg.memory.embedding import fine_tune as fine_tune_module
from synthorg.memory.embedding.cancellation import CancellationToken
from synthorg.memory.embedding.fine_tune import (
    _PASSAGE_MAX_LENGTH,
    _QUERY_MAX_LENGTH,
    FineTuneStage,
    _chunk_text,
    _compute_metrics,
    _generate_query,
    _scan_documents,
    deploy_checkpoint,
    generate_training_data,
)
from synthorg.memory.errors import (
    FineTuneCancelledError,
    FineTuneDependencyError,
)


class _FakeSentenceTransformersModule(Protocol):
    """Shape of the patched ``sentence_transformers`` module returned by the factory."""

    def SentenceTransformer(  # noqa: N802
        self, name: str, *, trust_remote_code: bool = False
    ) -> _RecordingEncoder: ...


class _EncodeCall(TypedDict):
    """One recorded ``encode()`` invocation."""

    model: str
    texts: list[str]
    kwargs: dict[str, object]


class _RecordingEncoder:
    """Fake ``SentenceTransformer`` that records every ``encode()`` call.

    ``encode_query`` and ``encode_document`` raise so the test fails loudly
    if production code switches to those alternate sentence-transformers APIs;
    these tests assert that the project calls ``encode()`` exclusively.
    """

    _EMBED_DIM = 8

    def __init__(self, name: str, calls: list[_EncodeCall]) -> None:
        self.name = name
        self._calls = calls

    def encode(self, texts: list[str], **kwargs: object) -> npt.NDArray[np.float32]:
        self._calls.append(
            {"model": self.name, "texts": list(texts), "kwargs": kwargs},
        )
        # ``max(len(texts), 1)`` guards against the degenerate ``np.eye(0, dim)``
        # shape; the trailing slice produces the correct ``(len(texts), dim)``.
        return np.eye(max(len(texts), 1), self._EMBED_DIM, dtype=np.float32)[
            : len(texts)
        ]

    def encode_query(
        self, *_args: object, **_kwargs: object
    ) -> npt.NDArray[np.float32]:
        msg = (
            "Production code must call encode() with processing_kwargs, "
            "not encode_query()."
        )
        raise AssertionError(msg)

    def encode_document(
        self, *_args: object, **_kwargs: object
    ) -> npt.NDArray[np.float32]:
        msg = (
            "Production code must call encode() with processing_kwargs, "
            "not encode_document()."
        )
        raise AssertionError(msg)


def _make_fake_st_module(
    calls: list[_EncodeCall],
) -> _FakeSentenceTransformersModule:
    """Build a ``SimpleNamespace`` fake of the ``sentence_transformers`` module."""
    fake = SimpleNamespace(
        SentenceTransformer=lambda name, **_kwargs: _RecordingEncoder(name, calls),
    )
    return cast("_FakeSentenceTransformersModule", fake)


def _expected_encode_kwargs(*, max_length: int) -> dict[str, object]:
    """Return the full kwargs dict the production code should pass to ``encode``."""
    return {
        "show_progress_bar": False,
        "processing_kwargs": {
            "text": {"max_length": max_length, "truncation": True},
        },
    }


def _index_calls(
    calls: list[_EncodeCall],
) -> dict[tuple[str, tuple[str, ...]], _EncodeCall]:
    """Index encode calls by ``(model_name, texts)`` for order-independent lookup."""
    return {(call["model"], tuple(call["texts"])): call for call in calls}


@pytest.mark.unit
class TestFineTuneStage:
    def test_values(self) -> None:
        assert FineTuneStage.IDLE.value == "idle"
        assert FineTuneStage.GENERATING_DATA.value == "generating_data"
        assert FineTuneStage.MINING_NEGATIVES.value == "mining_negatives"
        assert FineTuneStage.TRAINING.value == "training"
        assert FineTuneStage.EVALUATING.value == "evaluating"
        assert FineTuneStage.DEPLOYING.value == "deploying"
        assert FineTuneStage.COMPLETE.value == "complete"
        assert FineTuneStage.FAILED.value == "failed"


# -- Helpers ----------------------------------------------------------


@pytest.mark.unit
class TestChunkText:
    def test_basic_chunking(self) -> None:
        text = " ".join(f"word{i}" for i in range(20))
        chunks = _chunk_text(text, chunk_size=10)
        assert len(chunks) == 2

    def test_empty_text(self) -> None:
        assert _chunk_text("") == []

    def test_single_chunk(self) -> None:
        chunks = _chunk_text("hello world", chunk_size=100)
        assert len(chunks) == 1
        assert chunks[0] == "hello world"


@pytest.mark.unit
class TestGenerateQuery:
    def test_extractive_fallback(self) -> None:
        query = _generate_query("First sentence. Second.", None)
        assert "First sentence" in query


@pytest.mark.unit
class TestScanDocuments:
    def test_scans_text_files(self, tmp_path: Path) -> None:
        (tmp_path / "doc.txt").write_text("hello")
        (tmp_path / "readme.md").write_text("world")
        (tmp_path / "data.json").write_text("{}")  # not scanned
        results = _scan_documents(str(tmp_path))
        assert len(results) == 2

    def test_empty_dir(self, tmp_path: Path) -> None:
        assert _scan_documents(str(tmp_path)) == []

    def test_skips_empty_files(self, tmp_path: Path) -> None:
        (tmp_path / "empty.txt").write_text("")
        assert _scan_documents(str(tmp_path)) == []


# -- Stage 1: Generate training data ----------------------------------


@pytest.mark.unit
class TestGenerateTrainingData:
    async def test_rejects_blank_source_dir(self) -> None:
        with pytest.raises(ValueError, match="source_dir"):
            await generate_training_data(
                source_dir="   ",
                output_dir="/output",
            )

    async def test_rejects_blank_output_dir(self) -> None:
        with pytest.raises(ValueError, match="output_dir"):
            await generate_training_data(
                source_dir="/source",
                output_dir="   ",
            )

    async def test_no_documents_raises(self, tmp_path: Path) -> None:
        src = tmp_path / "empty_src"
        src.mkdir()
        out = tmp_path / "out"
        with pytest.raises(ValueError, match="No documents"):
            await generate_training_data(
                source_dir=str(src),
                output_dir=str(out),
            )

    async def test_generates_training_and_validation(
        self,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        # Need enough content for at least 2 chunks (512 words each).
        (src / "doc1.txt").write_text("This is test content. " * 300)
        (src / "doc2.txt").write_text("Another document here. " * 300)
        out = tmp_path / "out"
        train_path, val_path = await generate_training_data(
            source_dir=str(src),
            output_dir=str(out),
            validation_split=0.3,
        )
        assert train_path.exists()
        assert val_path.exists()
        train_lines = [ln for ln in train_path.read_text().splitlines() if ln.strip()]
        val_lines = [ln for ln in val_path.read_text().splitlines() if ln.strip()]
        assert len(train_lines) >= 1
        assert len(val_lines) >= 1
        # Validate JSONL format.
        pair = json.loads(train_lines[0])
        assert "query" in pair
        assert "positive_passage" in pair

    async def test_progress_callback_called(
        self,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("content one")
        (src / "b.txt").write_text("content two")
        out = tmp_path / "out"
        progress_values: list[float] = []
        await generate_training_data(
            source_dir=str(src),
            output_dir=str(out),
            progress_callback=progress_values.append,
        )
        assert len(progress_values) >= 2
        assert progress_values[-1] == 1.0

    async def test_cancellation_checked(
        self,
        tmp_path: Path,
    ) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "doc.txt").write_text("content")
        out = tmp_path / "out"
        token = CancellationToken()
        token.cancel()
        with pytest.raises(FineTuneCancelledError):
            await generate_training_data(
                source_dir=str(src),
                output_dir=str(out),
                cancellation=token,
            )


# -- Stage 2: Mine hard negatives (mock-based) -----------------------


@pytest.mark.unit
class TestMineHardNegatives:
    async def test_rejects_blank_training_data_path(self) -> None:
        from synthorg.memory.embedding.fine_tune import (
            mine_hard_negatives,
        )

        with pytest.raises(ValueError, match="training_data_path"):
            await mine_hard_negatives(
                training_data_path="   ",
                base_model="test-small-001",
                output_dir="/output",
            )

    async def test_dependency_error_without_sentence_transformers(
        self,
    ) -> None:
        from synthorg.memory.embedding.fine_tune import (
            mine_hard_negatives,
        )

        with (
            patch(
                "synthorg.memory.embedding.fine_tune._import_sentence_transformers",
                side_effect=FineTuneDependencyError("missing"),
            ),
            pytest.raises(FineTuneDependencyError),
        ):
            await mine_hard_negatives(
                training_data_path="/data/train.jsonl",
                base_model="test-small-001",
                output_dir="/output",
            )

    async def test_uses_per_call_max_length(self, tmp_path: Path) -> None:
        from synthorg.memory.embedding.fine_tune import (
            mine_hard_negatives,
        )

        train = tmp_path / "train.jsonl"
        train.write_text(
            json.dumps({"query": "q1", "positive_passage": "p1"})
            + "\n"
            + json.dumps({"query": "q2", "positive_passage": "p2"})
            + "\n",
        )
        calls: list[_EncodeCall] = []
        with patch(
            "synthorg.memory.embedding.fine_tune._import_sentence_transformers",
            return_value=_make_fake_st_module(calls),
        ):
            await mine_hard_negatives(
                training_data_path=str(train),
                base_model="test-small-001",
                output_dir=str(tmp_path / "out"),
            )

        assert len(calls) == 2
        indexed = _index_calls(calls)
        query_key = ("test-small-001", ("q1", "q2"))
        passage_key = ("test-small-001", ("p1", "p2"))
        assert query_key in indexed
        assert passage_key in indexed
        assert indexed[query_key]["kwargs"] == _expected_encode_kwargs(
            max_length=_QUERY_MAX_LENGTH,
        )
        assert indexed[passage_key]["kwargs"] == _expected_encode_kwargs(
            max_length=_PASSAGE_MAX_LENGTH,
        )

    async def test_emits_truncation_warning_for_long_query(
        self,
        tmp_path: Path,
    ) -> None:
        from synthorg.memory.embedding.fine_tune import (
            mine_hard_negatives,
        )

        long_query = " ".join(["word"] * 200)
        train = tmp_path / "train.jsonl"
        train.write_text(
            json.dumps({"query": long_query, "positive_passage": "p1"}) + "\n",
        )
        calls: list[_EncodeCall] = []
        with (
            patch(
                "synthorg.memory.embedding.fine_tune._import_sentence_transformers",
                return_value=_make_fake_st_module(calls),
            ),
            patch.object(fine_tune_module, "logger") as mock_logger,
        ):
            await mine_hard_negatives(
                training_data_path=str(train),
                base_model="test-small-001",
                output_dir=str(tmp_path / "out"),
            )

        truncation_events = [
            call
            for call in mock_logger.warning.call_args_list
            if call.args
            and call.args[0] == "memory.fine_tune.encode_truncation_likely"
            and call.kwargs.get("role") == "query"
        ]
        assert truncation_events, (
            "expected a truncation-likely warning for the long query input"
        )

    async def test_rejects_jsonl_record_missing_required_field(
        self,
        tmp_path: Path,
    ) -> None:
        from synthorg.memory.embedding.fine_tune import (
            mine_hard_negatives,
        )

        train = tmp_path / "train.jsonl"
        train.write_text(
            json.dumps({"query": "q1", "positive_passage": "p1"})
            + "\n"
            + json.dumps({"query": "q2"})
            + "\n",
        )
        calls: list[_EncodeCall] = []
        with (
            patch(
                "synthorg.memory.embedding.fine_tune._import_sentence_transformers",
                return_value=_make_fake_st_module(calls),
            ),
            pytest.raises(ValueError, match="index 1"),
        ):
            await mine_hard_negatives(
                training_data_path=str(train),
                base_model="test-small-001",
                output_dir=str(tmp_path / "out"),
            )

    async def test_rejects_jsonl_record_with_non_string_field(
        self,
        tmp_path: Path,
    ) -> None:
        from synthorg.memory.embedding.fine_tune import (
            mine_hard_negatives,
        )

        train = tmp_path / "train.jsonl"
        train.write_text(
            json.dumps({"query": "q1", "positive_passage": 42}) + "\n",
        )
        calls: list[_EncodeCall] = []
        with (
            patch(
                "synthorg.memory.embedding.fine_tune._import_sentence_transformers",
                return_value=_make_fake_st_module(calls),
            ),
            pytest.raises(TypeError, match="index 0"),
        ):
            await mine_hard_negatives(
                training_data_path=str(train),
                base_model="test-small-001",
                output_dir=str(tmp_path / "out"),
            )


# -- Stage 3: Contrastive fine-tuning (mock-based) -------------------


@pytest.mark.unit
class TestContrastiveFineTune:
    async def test_rejects_blank_training_data_path(self) -> None:
        from synthorg.memory.embedding.fine_tune import (
            contrastive_fine_tune,
        )

        with pytest.raises(ValueError, match="training_data_path"):
            await contrastive_fine_tune(
                training_data_path="   ",
                base_model="test-small-001",
                output_dir="/output",
            )

    @pytest.mark.parametrize(
        ("param", "value", "match"),
        [
            ("epochs", 0, "epochs"),
            ("batch_size", 0, "batch_size"),
            ("learning_rate", -0.001, "learning_rate"),
            ("temperature", 0.0, "temperature"),
        ],
    )
    async def test_rejects_invalid_hyperparameters(
        self,
        param: str,
        value: float,
        match: str,
    ) -> None:
        from synthorg.memory.embedding.fine_tune import (
            contrastive_fine_tune,
        )

        kwargs: dict[str, object] = {
            "training_data_path": "/data",
            "base_model": "test-small-001",
            "output_dir": "/output",
            param: value,
        }
        with pytest.raises(ValueError, match=match):
            # The parametrized (param, value) drives one hyperparameter
            # out of range; the dynamic key precludes a precise static type.
            await contrastive_fine_tune(**kwargs)  # type: ignore[arg-type]


# -- Stage 4: Evaluation (mock-based) --------------------------------


@pytest.mark.unit
class TestEvaluateCheckpoint:
    async def test_rejects_blank_checkpoint_path(self) -> None:
        from synthorg.memory.embedding.fine_tune import (
            evaluate_checkpoint,
        )

        with pytest.raises(ValueError, match="checkpoint_path"):
            await evaluate_checkpoint(
                checkpoint_path="   ",
                base_model="test-small-001",
                validation_data_path="/val.jsonl",
                output_dir="/out",
            )

    async def test_rejects_empty_validation_data(self) -> None:
        from synthorg.memory.embedding.fine_tune import (
            evaluate_checkpoint,
        )

        with (
            patch(
                "synthorg.memory.embedding.fine_tune._import_sentence_transformers",
            ),
            patch(
                "synthorg.memory.embedding.fine_tune._read_jsonl",
                return_value=[],
            ),
            pytest.raises(ValueError, match="empty"),
        ):
            await evaluate_checkpoint(
                checkpoint_path="/cp",
                base_model="test-small-001",
                validation_data_path="/val.jsonl",
                output_dir="/out",
            )

    async def test_uses_per_call_max_length(self, tmp_path: Path) -> None:
        from synthorg.memory.embedding.fine_tune import (
            evaluate_checkpoint,
        )

        val = tmp_path / "val.jsonl"
        val.write_text(
            json.dumps({"query": "q1", "positive_passage": "p1"})
            + "\n"
            + json.dumps({"query": "q2", "positive_passage": "p2"})
            + "\n",
        )
        cp = tmp_path / "checkpoint"
        cp.mkdir()
        calls: list[_EncodeCall] = []
        with patch(
            "synthorg.memory.embedding.fine_tune._import_sentence_transformers",
            return_value=_make_fake_st_module(calls),
        ):
            await evaluate_checkpoint(
                checkpoint_path=str(cp),
                base_model="test-small-001",
                validation_data_path=str(val),
                output_dir=str(tmp_path / "out"),
            )

        assert len(calls) == 4
        indexed = _index_calls(calls)
        query_kwargs = _expected_encode_kwargs(max_length=_QUERY_MAX_LENGTH)
        passage_kwargs = _expected_encode_kwargs(max_length=_PASSAGE_MAX_LENGTH)
        for model_name in (str(cp), "test-small-001"):
            assert indexed[(model_name, ("q1", "q2"))]["kwargs"] == query_kwargs
            assert indexed[(model_name, ("p1", "p2"))]["kwargs"] == passage_kwargs

    async def test_emits_truncation_warning_for_long_passage(
        self,
        tmp_path: Path,
    ) -> None:
        from synthorg.memory.embedding.fine_tune import (
            evaluate_checkpoint,
        )

        long_passage = " ".join(["word"] * 500)
        val = tmp_path / "val.jsonl"
        val.write_text(
            json.dumps({"query": "q1", "positive_passage": long_passage}) + "\n",
        )
        cp = tmp_path / "checkpoint"
        cp.mkdir()
        calls: list[_EncodeCall] = []
        with (
            patch(
                "synthorg.memory.embedding.fine_tune._import_sentence_transformers",
                return_value=_make_fake_st_module(calls),
            ),
            patch.object(fine_tune_module, "logger") as mock_logger,
        ):
            await evaluate_checkpoint(
                checkpoint_path=str(cp),
                base_model="test-small-001",
                validation_data_path=str(val),
                output_dir=str(tmp_path / "out"),
            )

        truncation_events = [
            call
            for call in mock_logger.warning.call_args_list
            if call.args
            and call.args[0] == "memory.fine_tune.encode_truncation_likely"
            and call.kwargs.get("role") == "passage"
        ]
        assert truncation_events, (
            "expected truncation-likely warning for the long passage input"
        )


# -- Stage 5: Deploy checkpoint ---------------------------------------


@pytest.mark.unit
class TestDeployCheckpoint:
    async def test_rejects_blank_checkpoint_path(self) -> None:
        with pytest.raises(ValueError, match="checkpoint_path"):
            await deploy_checkpoint(checkpoint_path="   ")

    async def test_rejects_nonexistent_path(
        self,
        tmp_path: Path,
    ) -> None:
        missing = tmp_path / "definitely_missing_dir"
        with pytest.raises(ValueError, match="does not exist"):
            await deploy_checkpoint(
                checkpoint_path=str(missing),
            )

    async def test_returns_none_without_settings_service(
        self,
        tmp_path: Path,
    ) -> None:
        cp_dir = tmp_path / "checkpoint"
        cp_dir.mkdir()
        result = await deploy_checkpoint(checkpoint_path=str(cp_dir))
        assert result is None
        # No backup file created when no settings_service.
        backup = cp_dir.parent / "backup_config.json"
        assert not backup.exists()


# -- Compute metrics --------------------------------------------------


@pytest.mark.unit
class TestComputeMetrics:
    def test_perfect_ranking(self) -> None:
        import numpy as np

        n = 5
        embs = np.eye(n, dtype=np.float32)
        ndcg, recall = _compute_metrics(embs, embs)
        assert ndcg == pytest.approx(1.0)
        assert recall == pytest.approx(1.0)

    def test_random_embeddings_positive(self) -> None:
        import numpy as np

        rng = np.random.default_rng(42)
        n = 20
        q = rng.random((n, 64)).astype(np.float32)
        p = rng.random((n, 64)).astype(np.float32)
        ndcg, recall = _compute_metrics(q, p)
        assert 0.0 <= ndcg <= 1.0
        assert 0.0 <= recall <= 1.0
