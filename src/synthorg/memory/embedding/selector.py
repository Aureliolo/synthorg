"""Embedding model selection from available models.

Combines the LMEB / MTEB / curated rankings (see :mod:`rankings`) to pick the
best embedder available in the operator's catalogue, and infers a deployment
tier from the provider preset + GPU availability.

Key contract: selection returns the operator's ACTUAL catalogue model id (e.g.
``qwen3-embedding:8b``), never the benchmark/family id, and uses the model's
ingest-discovered output dimensions when available (falling back to the
ranking's static dims only when they are not).
"""

import re

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.memory.embedding.rankings import (
    EMBEDDING_RANKINGS,
    DeploymentTier,
    EmbeddingModelRanking,
    EmbeddingRankingSource,
)
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_EMBEDDER_AUTO_SELECTED,
)

logger = get_logger(__name__)

# Provider preset names that indicate local/self-hosted deployment.
_LOCAL_PRESETS: frozenset[str] = frozenset(
    {
        "ollama",
        "lm-studio",
        "vllm",
    }
)

# Trailing size suffix on a model id (``-4b``, ``-300m``, ``-7b``) -- stripped
# so a family ranking matches every size variant in the catalogue.
_SIZE_SUFFIX_RE = re.compile(r"-\d+(?:\.\d+)?[bm]$")


class EmbeddingSelection(BaseModel):
    """A resolved embedding-model choice for the memory substrate.

    Attributes:
        model_id: The operator's actual catalogue model id to persist + serve.
        output_dims: Vector dimensions to use (ingest-discovered when known,
            else the ranking's static fallback).
        source: Which ranking source matched (lmeb/mteb/curated).
        ranking_model_id: The benchmark/family id that matched (for logging).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    model_id: NotBlankStr
    output_dims: int = Field(ge=1)
    source: EmbeddingRankingSource
    ranking_model_id: NotBlankStr


def _base_family(name: str) -> str:
    """Reduce a model id to its base family for matching.

    Drops the version/size tag after ``:`` / ``@`` and any trailing size
    suffix, so ``qwen3-embedding:8b`` and ``Qwen3-Embedding-4B`` both reduce to
    ``qwen3-embedding``.

    Returns:
        The lowercased base family.
    """
    base = re.split(r"[:@]", name.strip().lower(), maxsplit=1)[0]
    return _SIZE_SUFFIX_RE.sub("", base)


def _size_token(name: str) -> str | None:
    """Extract a model id's size token (e.g. ``4b``, ``300m``), if any.

    Returns:
        The lowercased size token without its separator, or ``None`` when the
        id carries no size suffix (a family/base id).
    """
    match = _SIZE_SUFFIX_RE.search(re.split(r"[:@]", name.strip().lower())[0])
    return match.group(0).lstrip("-") if match else None


def select_embedding_model(
    available_models: tuple[str, ...],
    *,
    deployment_tier: DeploymentTier | None = None,
    dims_by_model: dict[str, int] | None = None,
) -> EmbeddingSelection | None:
    """Select the best ranked embedder available in the catalogue.

    Walks the combined ranking (LMEB > MTEB > curated, best-first) and returns
    the first ranking whose family is present in ``available_models``. The
    returned ``model_id`` is the operator's catalogue id; ``output_dims`` is the
    ingest-discovered dimension for that id when supplied, else the ranking's
    static fallback.

    Args:
        available_models: Model identifiers discovered from the provider.
        deployment_tier: Optional tier filter; ignored if no ranked model
            matches the tier (so a CPU host still gets a selection).
        dims_by_model: Optional map of catalogue model id -> real embedding
            dimensions discovered at ingest, overriding the static dims.

    Returns:
        The selected embedder, or ``None`` when no ranked family is available.
    """
    dims_map = dims_by_model or {}
    avail = tuple((m, m.lower(), _base_family(m)) for m in available_models)
    # The tier acts as a PREFERENCE, not a hard gate: try the tier-filtered
    # pool first, then fall back to all tiers, so a CPU host whose only ranked
    # model is GPU-tier still gets a selection.
    pools: list[tuple[EmbeddingModelRanking, ...]] = []
    if deployment_tier is not None:
        pools.append(tuple(r for r in EMBEDDING_RANKINGS if r.tier == deployment_tier))
    pools.append(EMBEDDING_RANKINGS)
    for candidates in pools:
        for ranking in candidates:
            ranking_id_lower = ranking.model_id.lower()
            ranking_base = _base_family(ranking.model_id)
            # A size-specific benchmark id (``Qwen3-Embedding-4B``) must only
            # match the SAME size variant; the operator's ``:8b`` falls through
            # to the family entry, which carries the right dimensions.
            ranking_size = _size_token(ranking.model_id)
            for original, available_lower, available_base in avail:
                size_ok = ranking_size is None or ranking_size in available_lower
                if size_ok and (
                    ranking_id_lower in available_lower
                    or ranking_base in available_lower
                    or ranking_base == available_base
                ):
                    output_dims = dims_map.get(original) or ranking.output_dims
                    logger.debug(
                        MEMORY_EMBEDDER_AUTO_SELECTED,
                        ranking_model=ranking.model_id,
                        available_model=original,
                        source=ranking.source,
                        output_dims=output_dims,
                    )
                    return EmbeddingSelection(
                        model_id=original,
                        output_dims=output_dims,
                        source=ranking.source,
                        ranking_model_id=ranking.model_id,
                    )
    return None


def infer_deployment_tier(
    provider_preset_name: str | None,
    *,
    has_gpu: bool | None = None,
) -> DeploymentTier:
    """Infer the deployment tier from provider context.

    Args:
        provider_preset_name: Provider preset identifier (e.g. ``"ollama"``,
            ``"lm-studio"``). ``None`` defaults to ``GPU_CONSUMER``.
            Non-local/unknown provider names default to ``GPU_FULL`` (cloud).
        has_gpu: Whether the host has a GPU. Only meaningful for local
            providers. ``None`` means unknown (assumes GPU for local).

    Returns:
        The inferred deployment tier.
    """
    if provider_preset_name is None:
        return DeploymentTier.GPU_CONSUMER
    name_lower = provider_preset_name.lower()
    if name_lower in _LOCAL_PRESETS:
        if has_gpu is False:
            return DeploymentTier.CPU
        return DeploymentTier.GPU_CONSUMER
    return DeploymentTier.GPU_FULL
