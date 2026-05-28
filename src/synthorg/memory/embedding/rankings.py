"""LMEB ranking data for embedding model selection.

Static data from the LMEB paper (Zhao et al., March 2026) encoded as
frozen Pydantic models.  Scores are NDCG@10 with instruction prompts
(unless noted).  See ``docs/reference/embedding-evaluation.md`` for
the full analysis and tier recommendations.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr


class DeploymentTier(StrEnum):
    """Deployment resource tier for embedding model selection.

    Attributes:
        GPU_FULL: Full-resource server (7-12B models, data-centre GPU).
        GPU_CONSUMER: Consumer GPU (1-4B models, 16-24 GB VRAM).
        CPU: CPU-only or embedded deployment (< 1B models).
    """

    GPU_FULL = "gpu_full"
    GPU_CONSUMER = "gpu_consumer"
    CPU = "cpu"


class EmbeddingModelRanking(BaseModel):
    """LMEB benchmark ranking for a single embedding model.

    Attributes:
        model_id: Model identifier (matches discovery / Ollama name).
        params_billions: Approximate parameter count in billions.
        tier: Recommended deployment tier.
        episodic: NDCG@10 on LMEB episodic tasks (69 tasks).
        procedural: NDCG@10 on LMEB procedural tasks (67 tasks).
        dialogue: NDCG@10 on LMEB dialogue tasks (42 tasks).
        semantic: NDCG@10 on LMEB semantic tasks (15 tasks).
        overall: NDCG@10 overall across all LMEB tasks.
        use_instructions: Whether instruction prompts help this model.
        output_dims: Output embedding vector dimensions.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    model_id: NotBlankStr = Field(
        description="Model identifier",
    )
    params_billions: float = Field(
        gt=0,
        description="Parameter count in billions",
    )
    tier: DeploymentTier = Field(
        description="Recommended deployment tier",
    )
    episodic: float = Field(
        ge=0,
        le=100,
        description="NDCG@10 on episodic tasks",
    )
    procedural: float = Field(
        ge=0,
        le=100,
        description="NDCG@10 on procedural tasks",
    )
    dialogue: float = Field(
        ge=0,
        le=100,
        description="NDCG@10 on dialogue tasks",
    )
    semantic: float = Field(
        ge=0,
        le=100,
        description="NDCG@10 on semantic tasks",
    )
    overall: float = Field(
        ge=0,
        le=100,
        description="NDCG@10 overall",
    )
    use_instructions: bool = Field(
        description="Whether instruction prompts improve performance",
    )
    output_dims: int = Field(
        ge=1,
        description="Output embedding vector dimensions",
    )


# ── LMEB leaderboard (sorted by overall score descending) ────────
#
# Sources:
#   - LMEB paper Table 3 (Zhao et al., March 2026)
#   - docs/reference/embedding-evaluation.md
#   - Model cards for output dimensions
#
# Note: EmbeddingGemma-300M and Qwen3-Embedding-4B per-category
# scores are estimated from limited LMEB data -- only overall
# scores and procedural scores are directly sourced from the paper.
#
# Dimensions verified from model cards:
#   bge-multilingual-gemma2:  3584 (BAAI/bge-multilingual-gemma2)
#   NV-Embed-v2:              4096 (nvidia/NV-Embed-v2)
#   e5-mistral-7b-instruct:   4096 (intfloat/e5-mistral-7b-instruct)
#   Qwen3-Embedding-4B:       2560 (Qwen/Qwen3-Embedding-4B)
#   multilingual-e5-large-instruct: 1024 (intfloat)
#   EmbeddingGemma-300M:       768 (google/EmbeddingGemma-300M)

LMEB_RANKINGS: tuple[EmbeddingModelRanking, ...] = (
    EmbeddingModelRanking(
        model_id="bge-multilingual-gemma2",
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
)
