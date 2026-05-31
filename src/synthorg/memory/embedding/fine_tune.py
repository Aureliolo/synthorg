# module-kind: complex_service
"""Embedding fine-tuning pipeline stage functions.

Five-stage offline pipeline for domain-specific embedding fine-tuning:

1. Synthetic data generation (extractive fallback; LLM path stubbed)
2. Hard negative mining (base model embedding + similarity search)
3. Contrastive fine-tuning (InfoNCE loss, biencoder training)
4. Evaluation (NDCG@10, Recall@10 comparison)
5. Deploy (save checkpoint, update config)

ML dependencies (torch, sentence-transformers) are optional;
they are imported lazily inside stage functions.  Missing deps raise
``FineTuneDependencyError`` with install instructions.

One cohesive responsibility: run the offline fine-tune pipeline. The
five stages are intentionally co-located because they share the lazy
ML-dependency import guards, the encoder / pairs / triples helpers,
the progress-callback contract, and the cancellation-token check
points; splitting per stage would duplicate every helper across five
modules and break the cancellation continuity the orchestrator
relies on.
"""

import asyncio
import json
import math
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.memory.embedding.training_writer import split_and_write_pairs
from synthorg.memory.errors import FineTuneDependencyError
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_FINE_TUNE_BACKUP_READ_SKIPPED,
    MEMORY_FINE_TUNE_CHECKPOINT_DEPLOYED,
    MEMORY_FINE_TUNE_DEPENDENCY_MISSING,
    MEMORY_FINE_TUNE_ENCODE_INVOKED,
    MEMORY_FINE_TUNE_ENCODE_TRUNCATION_LIKELY,
    MEMORY_FINE_TUNE_EVAL_COMPLETED,
    MEMORY_FINE_TUNE_VALIDATION_FAILED,
)

if TYPE_CHECKING:
    from types import ModuleType

    from synthorg.memory.embedding.cancellation import CancellationToken
    from synthorg.memory.embedding.fine_tune_models import EvalMetrics

logger = get_logger(__name__)

ProgressCallback = Callable[[float], None]


class FineTuneStage(StrEnum):
    """Fine-tuning pipeline lifecycle state."""

    IDLE = "idle"
    GENERATING_DATA = "generating_data"
    MINING_NEGATIVES = "mining_negatives"
    TRAINING = "training"
    EVALUATING = "evaluating"
    DEPLOYING = "deploying"
    COMPLETE = "complete"
    FAILED = "failed"


# -- Lazy dependency helpers ------------------------------------------


_DOCKER_DEP_HINT = (
    "In a Docker-orchestrated install the backend spawns an ephemeral "
    "synthorg-fine-tune-gpu (default) or synthorg-fine-tune-cpu container "
    "on demand. Enable without re-init: `synthorg config set sandbox true "
    "&& synthorg config set fine_tuning true && synthorg config set "
    "fine_tuning_variant gpu && synthorg stop && synthorg start` "
    "(replace `gpu` with `cpu` on non-NVIDIA hosts). For hand-managed "
    "compose deployments see "
    "https://synthorg.io/docs/guides/deployment/#fine-tuning-optional."
)
_INPROCESS_DEP_HINT = (
    "For in-process execution install the extras directly: "
    "`pip install 'synthorg[fine-tune-gpu]'` or "
    "`pip install 'synthorg[fine-tune-cpu]'`."
)

_DEFAULT_CHUNK_SIZE_WORDS: Final[int] = 512
_DEFAULT_VALIDATION_SPLIT: Final[float] = 0.1
_DEFAULT_HARD_NEGATIVE_TOP_K: Final[int] = 4
_DEFAULT_TRAIN_EPOCHS: Final[int] = 3
_DEFAULT_TRAIN_LEARNING_RATE: Final[float] = 1e-5
_DEFAULT_TRAIN_TEMPERATURE: Final[float] = 0.02
_DEFAULT_TRAIN_BATCH_SIZE: Final[int] = 128
_DEFAULT_METRICS_K: Final[int] = 10
_QUERY_MAX_LENGTH: Final[int] = 128
_PASSAGE_MAX_LENGTH: Final[int] = 512
_TOKENS_PER_WORD: Final[float] = 1.33

_ENCODE_ROLE_QUERY: Final[str] = "query"
_ENCODE_ROLE_PASSAGE: Final[str] = "passage"


def _likely_truncated_count(texts: list[str], max_length: int) -> int:
    """Count texts whose word count suggests they will hit ``max_length`` tokens.

    Returns:
        Result of type ``int``.
    """
    word_threshold = max_length / _TOKENS_PER_WORD
    return sum(1 for text in texts if len(text.split()) > word_threshold)


async def _encode_with_observability(
    *,
    model: object,
    texts: list[str],
    max_length: int,
    role: str,
    model_name: str,
) -> object:
    """Run ``model.encode`` with per-call ``processing_kwargs`` and observability.

    Returns:
        Result of type ``object``.
    """
    logger.debug(
        MEMORY_FINE_TUNE_ENCODE_INVOKED,
        role=role,
        model=model_name,
        max_length=max_length,
        batch_size=len(texts),
    )
    truncated = _likely_truncated_count(texts, max_length)
    if truncated > 0:
        logger.warning(
            MEMORY_FINE_TUNE_ENCODE_TRUNCATION_LIKELY,
            role=role,
            model=model_name,
            max_length=max_length,
            batch_size=len(texts),
            likely_truncated_count=truncated,
        )
    return await asyncio.to_thread(
        model.encode,  # type: ignore[attr-defined]
        texts,
        show_progress_bar=False,
        processing_kwargs={
            "text": {"max_length": max_length, "truncation": True},
        },
    )


async def _encode_query_passage_pair(
    *,
    model: object,
    model_name: str,
    queries: list[str],
    passages: list[str],
    cancellation: CancellationToken | None,
) -> tuple[object, object]:
    """Encode queries and passages, honouring cancellation between the two calls.

    Returns:
        Tuple ``(object, object)``.
    """
    if cancellation is not None:
        cancellation.check()
    q_embs = await _encode_with_observability(
        model=model,
        texts=queries,
        max_length=_QUERY_MAX_LENGTH,
        role=_ENCODE_ROLE_QUERY,
        model_name=model_name,
    )
    if cancellation is not None:
        cancellation.check()
    p_embs = await _encode_with_observability(
        model=model,
        texts=passages,
        max_length=_PASSAGE_MAX_LENGTH,
        role=_ENCODE_ROLE_PASSAGE,
        model_name=model_name,
    )
    if cancellation is not None:
        cancellation.check()
    return q_embs, p_embs


_REQUIRED_PAIR_FIELDS: Final[tuple[str, ...]] = ("query", "positive_passage")


async def _load_query_passage_pairs(
    path: str,
    *,
    require_non_empty: bool,
) -> tuple[list[str], list[str]]:
    """Read JSONL ``{"query", "positive_passage"}`` records into parallel lists.

    Returns:
        Tuple ``(list[str], list[str])``.

    Raises:
        ValueError: If an argument fails domain validation.
        TypeError: If an argument has an unexpected type.
    """
    pairs = await asyncio.to_thread(_read_jsonl, Path(path))
    if require_non_empty and not pairs:
        msg = "Validation data is empty"
        raise ValueError(msg)
    queries: list[str] = []
    passages: list[str] = []
    for idx, pair in enumerate(pairs):
        missing = [f for f in _REQUIRED_PAIR_FIELDS if f not in pair]
        if missing:
            msg = (
                f"Invalid JSONL record at index {idx}: missing field(s) "
                f"{missing} (expected {list(_REQUIRED_PAIR_FIELDS)})"
            )
            logger.warning(
                MEMORY_FINE_TUNE_VALIDATION_FAILED,
                field="training_record",
                record_index=idx,
                missing_fields=missing,
            )
            raise ValueError(msg)
        query = pair["query"]
        passage = pair["positive_passage"]
        if not isinstance(query, str) or not isinstance(passage, str):
            msg = (
                f"Invalid JSONL record at index {idx}: query and "
                "positive_passage must be strings"
            )
            logger.warning(
                MEMORY_FINE_TUNE_VALIDATION_FAILED,
                field="training_record",
                record_index=idx,
                reason="non_string_field",
            )
            raise TypeError(msg)
        queries.append(query)
        passages.append(passage)
    return queries, passages


async def _persist_triples(
    triples: list[dict[str, object]],
    output_dir: str,
) -> Path:
    """Write mining triples to ``training_triples.jsonl`` under ``output_dir``.

    Returns:
        Result of type ``Path``.
    """
    out = _ensure_dir(output_dir)
    triples_path = out / "training_triples.jsonl"
    await asyncio.to_thread(_write_jsonl_any, triples_path, triples)
    return triples_path


def _import_sentence_transformers() -> ModuleType:
    """Lazy-import sentence-transformers with friendly error.

    Returns:
        Result of type ``ModuleType``.

    Raises:
        FineTuneDependencyError: If the related operation fails.
    """
    try:
        import sentence_transformers  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError as exc:
        msg = (
            "sentence-transformers is required for fine-tuning. "
            f"{_DOCKER_DEP_HINT} {_INPROCESS_DEP_HINT}"
        )
        logger.warning(
            MEMORY_FINE_TUNE_DEPENDENCY_MISSING,
            package="sentence-transformers",
        )
        raise FineTuneDependencyError(msg) from exc
    else:
        return sentence_transformers  # type: ignore[no-any-return]


def _import_torch() -> ModuleType:
    """Lazy-import torch with friendly error.

    Returns:
        Result of type ``ModuleType``.

    Raises:
        FineTuneDependencyError: If the related operation fails.
    """
    try:
        import torch  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError as exc:
        msg = (
            "torch is required for fine-tuning. "
            f"{_DOCKER_DEP_HINT} {_INPROCESS_DEP_HINT}"
        )
        logger.warning(
            MEMORY_FINE_TUNE_DEPENDENCY_MISSING,
            package="torch",
        )
        raise FineTuneDependencyError(msg) from exc
    else:
        return torch  # type: ignore[no-any-return]


# -- Validation helpers -----------------------------------------------


def _require_not_blank(value: str, name: str) -> None:
    """Raise ``ValueError`` if *value* is blank.

    Raises:
        ValueError: If an argument fails domain validation.
    """
    if not value.strip():
        msg = f"{name} must not be blank"
        logger.warning(
            MEMORY_FINE_TUNE_VALIDATION_FAILED,
            field=name,
            reason=msg,
        )
        raise ValueError(msg)


def _ensure_dir(path: str) -> Path:
    """Create directory if needed and return as Path.

    Returns:
        Result of type ``Path``.
    """
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


# -- Stage 1: Synthetic data generation -------------------------------


def _chunk_text(
    text: str,
    chunk_size: int = _DEFAULT_CHUNK_SIZE_WORDS,
) -> list[str]:
    """Split text into word-boundary chunks.

    Produces chunks of exactly *chunk_size* words
    (the last chunk may be shorter).

    Returns:
        List of ``str``.
    """
    words = text.split()
    chunks: list[str] = []
    for i in range(0, len(words), chunk_size):
        chunk = " ".join(words[i : i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)
    return chunks


def _scan_documents(source_dir: str) -> list[tuple[str, str]]:
    """Scan directory for text files, return (path, content) pairs.

    Returns:
        List of ``tuple[str, str]``.
    """
    src = Path(source_dir)
    results: list[tuple[str, str]] = []
    for ext in ("*.txt", "*.md", "*.rst"):
        for f in src.rglob(ext):
            try:
                content = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                logger.warning(
                    MEMORY_FINE_TUNE_VALIDATION_FAILED,
                    file=str(f),
                    reason="not valid UTF-8, skipping",
                )
                continue
            if content.strip():
                results.append((str(f), content))
    return results


async def generate_training_data(  # noqa: PLR0913
    source_dir: str,
    output_dir: str,
    *,
    llm_provider: object | None = None,
    validation_split: float = _DEFAULT_VALIDATION_SPLIT,
    progress_callback: ProgressCallback | None = None,
    cancellation: CancellationToken | None = None,
) -> tuple[Path, Path]:
    """Stage 1: Generate synthetic query-document pairs.

    Generate synthetic query-document pairs from source documents.
    No manual annotation required.

    When no ``llm_provider`` is available, generates simple
    extractive queries from chunk content.

    Args:
        source_dir: Directory containing org documents.
        output_dir: Directory to write training data.
        llm_provider: Optional LLM provider for generation.
        validation_split: Fraction held out for evaluation.
        progress_callback: Called with progress 0.0-1.0.
        cancellation: Checked between documents.

    Returns:
        Tuple of (training_path, validation_path).

    Raises:
        ValueError: If inputs are blank or no documents found.
    """
    _require_not_blank(source_dir, "source_dir")
    _require_not_blank(output_dir, "output_dir")

    docs = await asyncio.to_thread(_scan_documents, source_dir)
    if not docs:
        msg = f"No documents found in {source_dir}"
        raise ValueError(msg)

    all_pairs: list[dict[str, str]] = []
    for i, (_path, content) in enumerate(docs):
        if cancellation is not None:
            cancellation.check()
        chunks = _chunk_text(content)
        for chunk in chunks:
            query = _generate_query(chunk, llm_provider)
            all_pairs.append(
                {"query": query, "positive_passage": chunk},
            )
        if progress_callback:
            progress_callback((i + 1) / len(docs))

    return await split_and_write_pairs(
        all_pairs,
        output_dir,
        validation_split=validation_split,
    )


def _generate_query(
    chunk: str,
    llm_provider: object | None,
) -> str:
    """Generate an extractive retrieval query from a chunk.

    The *llm_provider* parameter is accepted for forward compatibility
    but is currently unused -- all queries use extractive fallback.

    Returns:
        Result of type ``str``.
    """
    # LLM-based generation would go here when provider protocol
    # is wired. For now, use extractive fallback.
    _ = llm_provider
    sentences = chunk.split(".")
    first = sentences[0].strip() if sentences else chunk[:100]
    if not first:
        first = chunk[:100].strip()
    first = first[:200]
    return f"Find information about: {first}"


# -- Stage 2: Hard negative mining ------------------------------------


async def mine_hard_negatives(  # noqa: PLR0913
    training_data_path: str,
    base_model: str,
    output_dir: str,
    *,
    top_k: int = _DEFAULT_HARD_NEGATIVE_TOP_K,
    progress_callback: ProgressCallback | None = None,
    cancellation: CancellationToken | None = None,
) -> Path:
    """Stage 2: Mine hard negatives using the base model.

    Embeds all passages with the base model and selects the top-k
    highest-scoring non-positive passages as hard negatives.

    Args:
        training_data_path: Path to training data from Stage 1.
        base_model: Base embedding model identifier.
        output_dir: Directory to write mined negatives.
        top_k: Number of hard negatives per query.
        progress_callback: Called with progress 0.0-1.0.
        cancellation: Checked between query batches.

    Returns:
        Path to the training triples file.

    Raises:
        ValueError: If inputs are blank.
        FineTuneDependencyError: If sentence-transformers missing.
    """
    _require_not_blank(training_data_path, "training_data_path")
    _require_not_blank(base_model, "base_model")
    _require_not_blank(output_dir, "output_dir")

    st = _import_sentence_transformers()
    queries, passages = await _load_query_passage_pairs(
        training_data_path,
        require_non_empty=False,
    )
    model = await asyncio.to_thread(st.SentenceTransformer, base_model)
    triples = await _mine_negatives_from_pairs(
        model=model,
        model_name=base_model,
        queries=queries,
        passages=passages,
        top_k=top_k,
        cancellation=cancellation,
        progress_callback=progress_callback,
    )
    return await _persist_triples(triples, output_dir)


async def _mine_negatives_from_pairs(  # noqa: PLR0913
    *,
    model: object,
    model_name: str,
    queries: list[str],
    passages: list[str],
    top_k: int,
    cancellation: CancellationToken | None,
    progress_callback: ProgressCallback | None,
) -> list[dict[str, object]]:
    """Encode the pairs and pick hard negatives in one orchestration.

    Returns:
        List of ``dict[str, object]``.
    """
    query_embeddings, passage_embeddings = await _encode_query_passage_pair(
        model=model,
        model_name=model_name,
        queries=queries,
        passages=passages,
        cancellation=cancellation,
    )
    return await _select_hard_negatives(
        queries=queries,
        passages=passages,
        query_embeddings=query_embeddings,
        passage_embeddings=passage_embeddings,
        top_k=top_k,
        cancellation=cancellation,
        progress_callback=progress_callback,
    )


_HARD_NEGATIVE_MARGIN_RATIO: Final[float] = 0.95
_CANCELLATION_CHECK_INTERVAL: Final[int] = 50


async def _select_hard_negatives(  # noqa: PLR0913
    *,
    queries: list[str],
    passages: list[str],
    query_embeddings: object,
    passage_embeddings: object,
    top_k: int,
    cancellation: CancellationToken | None,
    progress_callback: ProgressCallback | None,
) -> list[dict[str, object]]:
    """Pick the top-k hardest non-positive passages per query by similarity.

    Returns:
        List of ``dict[str, object]``.
    """
    triples: list[dict[str, object]] = []
    for i, query in enumerate(queries):
        if cancellation is not None and i % _CANCELLATION_CHECK_INTERVAL == 0:
            cancellation.check()
        sims = await asyncio.to_thread(
            _cosine_similarities,
            query_embeddings[i],  # type: ignore[index]
            passage_embeddings,
        )
        positive_sim = sims[i]
        margin = _HARD_NEGATIVE_MARGIN_RATIO * positive_sim
        candidates = sorted(
            ((j, s) for j, s in enumerate(sims) if j != i and s < margin),
            key=lambda x: x[1],
            reverse=True,
        )[:top_k]
        negatives = [passages[j] for j, _ in candidates]
        triples.append(
            {
                "query": query,
                "positive": passages[i],
                "negatives": negatives,
            },
        )
        if progress_callback:
            progress_callback((i + 1) / len(queries))
    return triples


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file into a list of dicts.

    Returns:
        List of ``dict[str, Any]``.
    """
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for raw_line in f:
            stripped = raw_line.strip()
            if stripped:
                records.append(json.loads(stripped))
    return records


def _write_jsonl_any(
    path: Path,
    records: list[dict[str, Any]],
) -> None:
    """Write records as JSONL."""
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


def _cosine_similarities(
    query_emb: object,
    passage_embs: object,
) -> list[float]:
    """Compute cosine similarities between query and passages.

    Returns:
        List of ``float``.
    """
    import numpy as np  # noqa: PLC0415

    q = np.array(query_emb, dtype=np.float32)
    p = np.array(passage_embs, dtype=np.float32)
    q_norm = q / (np.linalg.norm(q) + 1e-10)
    p_norms = p / (np.linalg.norm(p, axis=1, keepdims=True) + 1e-10)
    sims = p_norms @ q_norm
    return sims.tolist()  # type: ignore[no-any-return]


# -- Stage 3: Contrastive fine-tuning ---------------------------------


async def contrastive_fine_tune(  # noqa: PLR0913
    training_data_path: str,
    base_model: str,
    output_dir: str,
    *,
    epochs: int = _DEFAULT_TRAIN_EPOCHS,
    learning_rate: float = _DEFAULT_TRAIN_LEARNING_RATE,
    temperature: float = _DEFAULT_TRAIN_TEMPERATURE,
    batch_size: int = _DEFAULT_TRAIN_BATCH_SIZE,
    progress_callback: ProgressCallback | None = None,
    cancellation: CancellationToken | None = None,
) -> Path:
    """Stage 3: Contrastive fine-tuning with InfoNCE loss.

    Trains a biencoder on the training triples from Stage 2.

    Args:
        training_data_path: Path to training triples from Stage 2.
        base_model: Base embedding model identifier.
        output_dir: Directory to save the checkpoint.
        epochs: Number of training epochs.
        learning_rate: Learning rate.
        temperature: InfoNCE temperature parameter.
        batch_size: Training batch size.
        progress_callback: Called with progress 0.0-1.0.
        cancellation: Checked between batches.

    Returns:
        Path to the saved checkpoint directory.

    Raises:
        ValueError: If inputs are invalid.
        FineTuneDependencyError: If deps are missing.
    """
    _require_not_blank(training_data_path, "training_data_path")
    _require_not_blank(base_model, "base_model")
    _require_not_blank(output_dir, "output_dir")
    if epochs < 1:
        msg = "epochs must be >= 1"
        raise ValueError(msg)
    if batch_size < 1:
        msg = "batch_size must be >= 1"
        raise ValueError(msg)
    if learning_rate <= 0:
        msg = "learning_rate must be > 0"
        raise ValueError(msg)
    if temperature <= 0:
        msg = "temperature must be > 0"
        raise ValueError(msg)

    st = _import_sentence_transformers()
    _import_torch()

    triples = await asyncio.to_thread(_read_jsonl, Path(training_data_path))
    model = await asyncio.to_thread(st.SentenceTransformer, base_model)

    examples = _build_training_examples(st, triples)
    total_steps = math.ceil(len(examples) / batch_size) * epochs

    checkpoint_dir = _ensure_dir(output_dir) / "checkpoint"
    checkpoint_dir.mkdir(exist_ok=True)

    step = 0

    def _progress_hook(
        score: float,  # noqa: ARG001
        _epoch: int,
        steps: int,  # noqa: ARG001
    ) -> None:
        """Trainer callback: check cancellation and report progress."""
        nonlocal step
        step += 1
        if cancellation is not None and step % 10 == 0:
            cancellation.check()
        if progress_callback:
            progress_callback(min(step / max(total_steps, 1), 1.0))

    loss = st.losses.MultipleNegativesRankingLoss(
        model=model,
        scale=1.0 / temperature,
    )
    train_dataloader = st.datasets.NoDuplicatesDataLoader(
        examples,
        batch_size=batch_size,
    )

    await asyncio.to_thread(
        model.fit,
        train_objectives=[(train_dataloader, loss)],
        epochs=epochs,
        warmup_steps=min(100, total_steps // 10),
        optimizer_params={"lr": learning_rate},
        callback=_progress_hook,
        show_progress_bar=False,
    )

    await asyncio.to_thread(model.save, str(checkpoint_dir))
    return checkpoint_dir


def _build_training_examples(
    st: ModuleType,
    triples: list[dict[str, Any]],
) -> list[object]:
    """Build sentence-transformers InputExample from triples.

    Returns:
        List of ``object``.
    """
    examples = []
    for triple in triples:
        query = str(triple["query"])
        positive = str(triple["positive"])
        negatives = triple.get("negatives", [])
        texts = [query, positive]
        if isinstance(negatives, list):
            texts.extend(str(n) for n in negatives)
        examples.append(st.InputExample(texts=texts))
    return examples


# -- Stage 4: Evaluation ----------------------------------------------


async def evaluate_checkpoint(  # noqa: PLR0913
    checkpoint_path: str,
    base_model: str,
    validation_data_path: str,
    output_dir: str,
    *,
    progress_callback: ProgressCallback | None = None,
    cancellation: CancellationToken | None = None,
) -> EvalMetrics:
    """Stage 4: Evaluate fine-tuned vs base model.

    Computes NDCG@10 and Recall@10 on validation data for both
    the fine-tuned and base models.

    Args:
        checkpoint_path: Path to fine-tuned model checkpoint.
        base_model: Base model identifier.
        validation_data_path: Path to validation.jsonl.
        output_dir: Directory to save eval_metrics.json.
        progress_callback: Called with progress 0.0-1.0.
        cancellation: Checked between batches.

    Returns:
        Evaluation metrics comparing fine-tuned vs base.
    """
    _require_not_blank(checkpoint_path, "checkpoint_path")
    _require_not_blank(base_model, "base_model")
    _require_not_blank(validation_data_path, "validation_data_path")
    _require_not_blank(output_dir, "output_dir")

    queries, passages = await _load_query_passage_pairs(
        validation_data_path,
        require_non_empty=True,
    )
    return await _run_eval_pipeline(
        checkpoint_path=checkpoint_path,
        base_model=base_model,
        output_dir=output_dir,
        queries=queries,
        passages=passages,
        cancellation=cancellation,
        progress_callback=progress_callback,
    )


_EVAL_PROGRESS_AFTER_LOAD: Final[float] = 0.2
_EVAL_PROGRESS_AFTER_FINETUNED: Final[float] = 0.5
_EVAL_PROGRESS_AFTER_BASE: Final[float] = 0.8
_EVAL_PROGRESS_COMPLETE: Final[float] = 1.0


def _report_progress(callback: ProgressCallback | None, value: float) -> None:
    """Invoke ``callback`` with ``value`` when a callback was supplied."""
    if callback is not None:
        callback(value)


async def _run_eval_pipeline(  # noqa: PLR0913
    *,
    checkpoint_path: str,
    base_model: str,
    output_dir: str,
    queries: list[str],
    passages: list[str],
    cancellation: CancellationToken | None,
    progress_callback: ProgressCallback | None,
) -> EvalMetrics:
    """Load both models, encode query/passage pairs, persist metrics.

    Returns:
        Result of type ``EvalMetrics``.
    """
    st = _import_sentence_transformers()
    from synthorg.memory.embedding.fine_tune_models import (  # noqa: PLC0415
        EvalMetrics,
    )

    finetuned = await asyncio.to_thread(st.SentenceTransformer, checkpoint_path)
    base = await asyncio.to_thread(st.SentenceTransformer, base_model)
    if cancellation is not None:
        cancellation.check()
    _report_progress(progress_callback, _EVAL_PROGRESS_AFTER_LOAD)
    ft_q_embs, ft_p_embs = await _encode_query_passage_pair(
        model=finetuned,
        model_name=checkpoint_path,
        queries=queries,
        passages=passages,
        cancellation=cancellation,
    )
    _report_progress(progress_callback, _EVAL_PROGRESS_AFTER_FINETUNED)
    base_q_embs, base_p_embs = await _encode_query_passage_pair(
        model=base,
        model_name=base_model,
        queries=queries,
        passages=passages,
        cancellation=cancellation,
    )
    _report_progress(progress_callback, _EVAL_PROGRESS_AFTER_BASE)
    metrics = await _persist_eval_metrics(
        ft_q_embs=ft_q_embs,
        ft_p_embs=ft_p_embs,
        base_q_embs=base_q_embs,
        base_p_embs=base_p_embs,
        output_dir=output_dir,
        eval_metrics_cls=EvalMetrics,
    )
    _report_progress(progress_callback, _EVAL_PROGRESS_COMPLETE)
    return metrics


async def _persist_eval_metrics(  # noqa: PLR0913
    *,
    ft_q_embs: object,
    ft_p_embs: object,
    base_q_embs: object,
    base_p_embs: object,
    output_dir: str,
    eval_metrics_cls: type[EvalMetrics],
) -> EvalMetrics:
    """Compute eval metrics, write the JSON file, and emit the completion log.

    Returns:
        Result of type ``EvalMetrics``.
    """
    ft_ndcg, ft_recall = _compute_metrics(ft_q_embs, ft_p_embs)
    base_ndcg, base_recall = _compute_metrics(base_q_embs, base_p_embs)

    metrics = eval_metrics_cls(
        ndcg_at_10=ft_ndcg,
        recall_at_10=ft_recall,
        base_ndcg_at_10=base_ndcg,
        base_recall_at_10=base_recall,
    )

    out = _ensure_dir(output_dir)
    metrics_path = out / "eval_metrics.json"
    await asyncio.to_thread(
        metrics_path.write_text,
        metrics.model_dump_json(indent=2),
    )

    logger.info(
        MEMORY_FINE_TUNE_EVAL_COMPLETED,
        ndcg_at_10=ft_ndcg,
        recall_at_10=ft_recall,
        improvement_ndcg=metrics.improvement_ndcg,
        improvement_recall=metrics.improvement_recall,
    )
    return metrics


def _compute_metrics(
    query_embs: object,
    passage_embs: object,
    k: int = _DEFAULT_METRICS_K,
) -> tuple[float, float]:
    """Compute NDCG@k and Recall@k.

    Each query's ground truth is the passage at the same index.

    Returns:
        Tuple ``(float, float)``.
    """
    import numpy as np  # noqa: PLC0415

    q = np.array(query_embs, dtype=np.float32)
    p = np.array(passage_embs, dtype=np.float32)
    q_norms = q / (np.linalg.norm(q, axis=1, keepdims=True) + 1e-10)
    p_norms = p / (np.linalg.norm(p, axis=1, keepdims=True) + 1e-10)
    sim_matrix = q_norms @ p_norms.T

    n = len(q)
    ndcg_sum = 0.0
    recall_sum = 0.0
    for i in range(n):
        ranked = np.argsort(-sim_matrix[i])[:k]
        if i in ranked:
            rank_pos = int(np.where(ranked == i)[0][0])
            ndcg_sum += 1.0 / math.log2(rank_pos + 2)
            recall_sum += 1.0
    ideal_dcg = 1.0  # single relevant doc at rank 1
    ndcg = (ndcg_sum / n) / ideal_dcg if n > 0 else 0.0
    recall = recall_sum / n if n > 0 else 0.0
    return min(ndcg, 1.0), min(recall, 1.0)


# -- Stage 5: Deploy checkpoint ----------------------------------------


async def deploy_checkpoint(
    checkpoint_path: str,
    config_path: str | None = None,
    *,
    settings_service: object | None = None,
) -> str | None:
    """Stage 5: Deploy a fine-tuned checkpoint.

    Backs up current embedder config and updates it to point
    to the fine-tuned model.

    Args:
        checkpoint_path: Path to the fine-tuned model checkpoint.
        config_path: Optional config file to update.
        settings_service: Optional settings service for runtime
            config updates.

    Returns:
        JSON string of the pre-deployment backup config, or ``None``.

    Raises:
        ValueError: If checkpoint_path is blank.
    """
    _require_not_blank(checkpoint_path, "checkpoint_path")

    cp = Path(checkpoint_path)
    exists = await asyncio.to_thread(cp.exists)
    if not exists:
        msg = f"Checkpoint path does not exist: {checkpoint_path}"
        raise ValueError(msg)

    if config_path is not None and settings_service is None:
        logger.warning(
            MEMORY_FINE_TUNE_VALIDATION_FAILED,
            field="config_path",
            reason="config_path provided without settings_service"
            " -- file-based config update not implemented",
        )
        return None

    # Back up current config if settings service is available.
    backup: dict[str, str] = {}
    if settings_service is not None and hasattr(
        settings_service,
        "get",
    ):
        for key in (
            "embedder_provider",
            "embedder_model",
            "embedder_dims",
        ):
            try:
                sv = await settings_service.get("memory", key)
                if sv and hasattr(sv, "value") and sv.value:
                    backup[key] = sv.value
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    MEMORY_FINE_TUNE_BACKUP_READ_SKIPPED,
                    key=key,
                )

    # Only proceed with deployment if we have a settings service.
    if settings_service is None:
        logger.warning(
            MEMORY_FINE_TUNE_CHECKPOINT_DEPLOYED,
            checkpoint_path=checkpoint_path,
            note="no settings service -- checkpoint deployed but config not updated",
        )
        return json.dumps(backup) if backup else None

    # Write backup to checkpoint dir for rollback.
    backup_path = cp.parent / "backup_config.json"
    await asyncio.to_thread(
        backup_path.write_text,
        json.dumps(backup, indent=2),
    )

    # Update settings to point to the fine-tuned model.
    if hasattr(settings_service, "set"):
        await settings_service.set(
            "memory",
            "embedder_model",
            checkpoint_path,
        )

    logger.info(
        MEMORY_FINE_TUNE_CHECKPOINT_DEPLOYED,
        checkpoint_path=checkpoint_path,
        config_path=config_path,
        backup_keys=list(backup.keys()),
    )
    return json.dumps(backup) if backup else None
