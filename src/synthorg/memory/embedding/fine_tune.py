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
"""

import asyncio
import json
import math
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from synthorg.memory.errors import FineTuneDependencyError
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_FINE_TUNE_BACKUP_READ_SKIPPED,
    MEMORY_FINE_TUNE_CHECKPOINT_DEPLOYED,
    MEMORY_FINE_TUNE_DEPENDENCY_MISSING,
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


def _import_sentence_transformers() -> ModuleType:
    """Lazy-import sentence-transformers with friendly error."""
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
    """Lazy-import torch with friendly error."""
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
    """Raise ``ValueError`` if *value* is blank."""
    if not value.strip():
        msg = f"{name} must not be blank"
        logger.warning(
            MEMORY_FINE_TUNE_VALIDATION_FAILED,
            field=name,
            reason=msg,
        )
        raise ValueError(msg)


def _ensure_dir(path: str) -> Path:
    """Create directory if needed and return as Path."""
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
    """
    words = text.split()
    chunks: list[str] = []
    for i in range(0, len(words), chunk_size):
        chunk = " ".join(words[i : i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)
    return chunks


def _scan_documents(source_dir: str) -> list[tuple[str, str]]:
    """Scan directory for text files, return (path, content) pairs."""
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

    if validation_split <= 0.0 or validation_split >= 1.0:
        msg = (
            f"validation_split must be between 0 and 1 exclusive, "
            f"got {validation_split}"
        )
        raise ValueError(msg)

    out = _ensure_dir(output_dir)
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

    if len(all_pairs) < 2:  # noqa: PLR2004
        msg = (
            f"Need at least 2 query-document pairs for "
            f"train/validation split, got {len(all_pairs)}"
        )
        raise ValueError(msg)
    raw_split = int(len(all_pairs) * (1 - validation_split))
    split_idx = max(1, min(len(all_pairs) - 1, raw_split))
    training = all_pairs[:split_idx]
    validation = all_pairs[split_idx:]

    train_path = out / "training.jsonl"
    val_path = out / "validation.jsonl"
    await asyncio.to_thread(_write_jsonl_any, train_path, training)
    await asyncio.to_thread(_write_jsonl_any, val_path, validation)

    return train_path, val_path


def _generate_query(
    chunk: str,
    llm_provider: object | None,
) -> str:
    """Generate an extractive retrieval query from a chunk.

    The *llm_provider* parameter is accepted for forward compatibility
    but is currently unused -- all queries use extractive fallback.
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
    pairs = await asyncio.to_thread(_read_jsonl, Path(training_data_path))
    passages = [p["positive_passage"] for p in pairs]
    queries = [p["query"] for p in pairs]

    model = await asyncio.to_thread(st.SentenceTransformer, base_model)
    passage_embeddings = await asyncio.to_thread(
        model.encode,
        passages,
        show_progress_bar=False,
        processing_kwargs={
            "text": {"max_length": _PASSAGE_MAX_LENGTH, "truncation": True},
        },
    )

    out = _ensure_dir(output_dir)
    triples_path = out / "training_triples.jsonl"

    query_embeddings = await asyncio.to_thread(
        model.encode,
        queries,
        show_progress_bar=False,
        processing_kwargs={
            "text": {"max_length": _QUERY_MAX_LENGTH, "truncation": True},
        },
    )

    triples: list[dict[str, object]] = []
    for i, query in enumerate(queries):
        if cancellation is not None and i % 50 == 0:
            cancellation.check()
        sims = await asyncio.to_thread(
            _cosine_similarities,
            query_embeddings[i],
            passage_embeddings,
        )
        positive_sim = sims[i]
        margin = 0.95 * positive_sim
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

    await asyncio.to_thread(_write_jsonl_any, triples_path, triples)
    return triples_path


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file into a list of dicts."""
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
    """Compute cosine similarities between query and passages."""
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
        epoch: int,  # noqa: ARG001
        steps: int,  # noqa: ARG001
    ) -> None:
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
    """Build sentence-transformers InputExample from triples."""
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

    st = _import_sentence_transformers()
    from synthorg.memory.embedding.fine_tune_models import (  # noqa: PLC0415
        EvalMetrics,
    )

    pairs = await asyncio.to_thread(_read_jsonl, Path(validation_data_path))
    if not pairs:
        msg = "Validation data is empty"
        raise ValueError(msg)

    queries = [p["query"] for p in pairs]
    passages = [p["positive_passage"] for p in pairs]

    finetuned = await asyncio.to_thread(
        st.SentenceTransformer,
        checkpoint_path,
    )
    base = await asyncio.to_thread(st.SentenceTransformer, base_model)

    if cancellation is not None:
        cancellation.check()
    if progress_callback:
        progress_callback(0.2)

    ft_q_embs = await asyncio.to_thread(
        finetuned.encode,
        queries,
        show_progress_bar=False,
        processing_kwargs={
            "text": {"max_length": _QUERY_MAX_LENGTH, "truncation": True},
        },
    )
    if cancellation is not None:
        cancellation.check()
    ft_p_embs = await asyncio.to_thread(
        finetuned.encode,
        passages,
        show_progress_bar=False,
        processing_kwargs={
            "text": {"max_length": _PASSAGE_MAX_LENGTH, "truncation": True},
        },
    )
    if cancellation is not None:
        cancellation.check()
    if progress_callback:
        progress_callback(0.5)

    base_q_embs = await asyncio.to_thread(
        base.encode,
        queries,
        show_progress_bar=False,
        processing_kwargs={
            "text": {"max_length": _QUERY_MAX_LENGTH, "truncation": True},
        },
    )
    if cancellation is not None:
        cancellation.check()
    base_p_embs = await asyncio.to_thread(
        base.encode,
        passages,
        show_progress_bar=False,
        processing_kwargs={
            "text": {"max_length": _PASSAGE_MAX_LENGTH, "truncation": True},
        },
    )
    if progress_callback:
        progress_callback(0.8)

    ft_ndcg, ft_recall = _compute_metrics(ft_q_embs, ft_p_embs)
    base_ndcg, base_recall = _compute_metrics(
        base_q_embs,
        base_p_embs,
    )

    metrics = EvalMetrics(
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
    if progress_callback:
        progress_callback(1.0)

    return metrics


def _compute_metrics(
    query_embs: object,
    passage_embs: object,
    k: int = _DEFAULT_METRICS_K,
) -> tuple[float, float]:
    """Compute NDCG@k and Recall@k.

    Each query's ground truth is the passage at the same index.
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
            except MemoryError, RecursionError:
                raise
            except Exception:
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
