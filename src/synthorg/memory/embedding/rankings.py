"""Embedding-model ranking data for the memory embedder selection.

Three ranking sources are combined, applied in priority order:

1. **LMEB** (Long-horizon Memory Embedding Benchmark, Zhao et al., arXiv
   2603.12572, March 2026) -- the PRIMARY signal. LMEB measures long-horizon
   *memory* retrieval (fragmented, context-dependent, temporally distant
   recall), which is exactly what the agent-memory substrate does. The paper
   shows LMEB and MTEB measure orthogonal capabilities, so LMEB is the right
   signal for this use case. Scores are NDCG@10 with instruction prompts.
2. **MTEB v2** (Massive Text Embedding Benchmark) -- a SECONDARY signal for
   strong models LMEB does not rank, scored on general retrieval.
3. **Self-curated local tier** -- the embedders operators actually run locally
   (Ollama / LM Studio), with verified output dimensions and a hand-set order.
   No benchmark score is fabricated for these; they rank below benchmarked
   models but cover real catalogues (and the size variants LMEB omits).

Every entry carries a verified ``output_dims`` (the vector-store dimension) as
a STATIC FALLBACK; dimensions discovered live at ingest (e.g. Ollama's
``/api/show`` ``embedding_length``) override it, and MRL-capable models can be
truncated below it at runtime. ``select_embedding_model`` ranks across all
three sources and returns the operator's ACTUAL catalogue model id, never the
benchmark id.

See docs/reference/embedding-evaluation.md for the full analysis.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr


class DeploymentTier(StrEnum):
    """Deployment tier for an embedding model.

    Attributes:
        GPU_FULL: Full datacentre GPU (7B+ models, 40 GB+ VRAM).
        GPU_CONSUMER: Consumer GPU (1-8B models, 16-24 GB VRAM).
        CPU: CPU-only or embedded deployment (< 1B models).
    """

    GPU_FULL = "gpu_full"
    GPU_CONSUMER = "gpu_consumer"
    CPU = "cpu"


EmbeddingRankingSource = Literal["lmeb", "mteb", "curated"]
"""Provenance of an embedding ranking entry (drives the priority order)."""


class EmbeddingModelRanking(BaseModel):
    """Ranking record for a single embedding model.

    Attributes:
        model_id: Benchmark/family identifier, matched case-insensitively
            against discovered provider model ids (which carry version/size
            tags like ``:8b``).
        tier: Recommended deployment tier.
        output_dims: Output embedding vector dimensions (static fallback;
            ingest-discovered dims override this).
        source: Which ranking the entry comes from (lmeb/mteb/curated).
        params_billions: Approximate parameter count in billions, when known.
        use_instructions: Whether instruction prompts help this model.
        overall: LMEB NDCG@10 overall (only for ``source == "lmeb"``).
        episodic / procedural / dialogue / semantic: LMEB per-task NDCG@10.
        mteb_score: MTEB v2 score (only when on the MTEB leaderboard).
        curated_rank: Hand-set order within the curated tier (lower preferred).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    model_id: NotBlankStr = Field(description="Benchmark/family model identifier")
    tier: DeploymentTier = Field(description="Recommended deployment tier")
    output_dims: int = Field(ge=1, description="Output embedding vector dimensions")
    source: EmbeddingRankingSource = Field(
        default="curated",
        description="Ranking provenance (lmeb > mteb > curated priority)",
    )
    params_billions: float | None = Field(
        default=None,
        gt=0,
        description="Parameter count in billions, when known",
    )
    use_instructions: bool = Field(
        default=False,
        description="Whether instruction prompts improve performance",
    )
    overall: float | None = Field(
        default=None, ge=0, le=100, description="LMEB NDCG@10 overall"
    )
    episodic: float | None = Field(default=None, ge=0, le=100)
    procedural: float | None = Field(default=None, ge=0, le=100)
    dialogue: float | None = Field(default=None, ge=0, le=100)
    semantic: float | None = Field(default=None, ge=0, le=100)
    mteb_score: float | None = Field(
        default=None, ge=0, le=100, description="MTEB v2 score, when ranked"
    )
    curated_rank: int | None = Field(
        default=None, ge=0, description="Order within the curated tier"
    )


def _ranking_sort_key(ranking: EmbeddingModelRanking) -> tuple[int, float]:
    """Sort key applying the LMEB > MTEB > curated priority, best-first.

    Returns:
        A ``(group, -score)`` tuple; the scales are NOT mixed across groups
        (LMEB and MTEB are orthogonal), so grouping comes first.
    """
    group = {"lmeb": 0, "mteb": 1, "curated": 2}[ranking.source]
    if ranking.source == "lmeb":
        return (group, -(ranking.overall or 0.0))
    if ranking.source == "mteb":
        return (group, -(ranking.mteb_score or 0.0))
    rank = ranking.curated_rank if ranking.curated_rank is not None else 999
    return (group, float(rank))


# ── Combined embedding ranking catalogue ─────────────────────────
#
# LMEB sources: LMEB paper Table 3 (Zhao et al., arXiv 2603.12572, March 2026).
# MTEB sources: MTEB v2 multilingual leaderboard (2026).
# Curated dims: verified from model cards / Ollama model pages.
_RANKINGS_UNSORTED: tuple[EmbeddingModelRanking, ...] = (
    # ── LMEB (primary: long-horizon memory retrieval) ──
    EmbeddingModelRanking(
        model_id="bge-multilingual-gemma2",
        source="lmeb",
        params_billions=9.0,
        tier=DeploymentTier.GPU_FULL,
        episodic=70.88,
        procedural=61.40,
        dialogue=59.60,
        semantic=60.41,
        overall=61.41,
        use_instructions=True,
        output_dims=3584,
    ),
    EmbeddingModelRanking(
        model_id="NV-Embed-v2",
        source="lmeb",
        params_billions=7.0,
        tier=DeploymentTier.GPU_FULL,
        episodic=68.45,
        procedural=58.77,
        dialogue=56.42,
        semantic=62.18,
        overall=60.25,
        use_instructions=True,
        output_dims=4096,
    ),
    EmbeddingModelRanking(
        model_id="Qwen3-Embedding-4B",
        source="lmeb",
        params_billions=4.0,
        tier=DeploymentTier.GPU_CONSUMER,
        episodic=65.50,
        procedural=59.81,
        dialogue=54.20,
        semantic=55.80,
        overall=58.00,
        use_instructions=True,
        output_dims=2560,
    ),
    EmbeddingModelRanking(
        model_id="e5-mistral-7b-instruct",
        source="lmeb",
        params_billions=7.0,
        tier=DeploymentTier.GPU_FULL,
        episodic=67.43,
        procedural=55.41,
        dialogue=55.03,
        semantic=57.63,
        overall=57.08,
        use_instructions=True,
        output_dims=4096,
    ),
    EmbeddingModelRanking(
        model_id="EmbeddingGemma-300M",
        source="lmeb",
        params_billions=0.307,
        tier=DeploymentTier.CPU,
        episodic=58.00,
        procedural=53.50,
        dialogue=52.80,
        semantic=55.20,
        overall=56.03,
        use_instructions=False,
        output_dims=768,
    ),
    EmbeddingModelRanking(
        model_id="multilingual-e5-large-instruct",
        source="lmeb",
        params_billions=0.560,
        tier=DeploymentTier.CPU,
        episodic=63.60,
        procedural=52.22,
        dialogue=54.62,
        semantic=57.18,
        overall=55.33,
        use_instructions=True,
        output_dims=1024,
    ),
    # ── MTEB v2 (secondary: strong models LMEB does not rank) ──
    # Qwen3-Embedding-8B is #1 on the MTEB multilingual leaderboard and is the
    # family pulled by `ollama pull qwen3-embedding`. MRL-capable (selectable
    # 32..4096); 4096 is the native dim used as the static fallback.
    EmbeddingModelRanking(
        model_id="qwen3-embedding",
        source="mteb",
        params_billions=8.0,
        tier=DeploymentTier.GPU_CONSUMER,
        mteb_score=70.58,
        use_instructions=True,
        output_dims=4096,
    ),
    # ── Self-curated local tier (Ollama / LM Studio pulls) ──
    EmbeddingModelRanking(
        model_id="bge-m3",
        source="curated",
        curated_rank=0,
        params_billions=0.567,
        tier=DeploymentTier.GPU_CONSUMER,
        use_instructions=False,
        output_dims=1024,
    ),
    EmbeddingModelRanking(
        model_id="mxbai-embed-large",
        source="curated",
        curated_rank=1,
        params_billions=0.335,
        tier=DeploymentTier.CPU,
        use_instructions=False,
        output_dims=1024,
    ),
    EmbeddingModelRanking(
        model_id="snowflake-arctic-embed2",
        source="curated",
        curated_rank=2,
        params_billions=0.568,
        tier=DeploymentTier.CPU,
        use_instructions=False,
        output_dims=1024,
    ),
    EmbeddingModelRanking(
        model_id="nomic-embed-text",
        source="curated",
        curated_rank=3,
        params_billions=0.137,
        tier=DeploymentTier.CPU,
        use_instructions=False,
        output_dims=768,
    ),
    EmbeddingModelRanking(
        model_id="all-minilm",
        source="curated",
        curated_rank=4,
        params_billions=0.023,
        tier=DeploymentTier.CPU,
        use_instructions=False,
        output_dims=384,
    ),
)

EMBEDDING_RANKINGS: tuple[EmbeddingModelRanking, ...] = tuple(
    sorted(_RANKINGS_UNSORTED, key=_ranking_sort_key)
)
"""All embedding rankings, ordered LMEB > MTEB > curated, best-first."""

# Retained alias: the LMEB-only subset (some callers/tests reference it).
LMEB_RANKINGS: tuple[EmbeddingModelRanking, ...] = tuple(
    r for r in EMBEDDING_RANKINGS if r.source == "lmeb"
)
