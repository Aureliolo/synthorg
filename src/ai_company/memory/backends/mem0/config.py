"""Mem0 backend configuration and config builder.

Isolates Mem0-specific settings from the core ``CompanyMemoryConfig``.
The ``build_mem0_config_dict`` function produces the dict that Mem0's
``Memory.from_config()`` expects.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ai_company.core.types import NotBlankStr  # noqa: TC001
from ai_company.memory.config import CompanyMemoryConfig  # noqa: TC001


class Mem0EmbedderConfig(BaseModel):
    """Embedder settings for Mem0.

    Attributes:
        provider: Embedding provider name.
        model: Embedding model identifier.
        dims: Embedding vector dimensions.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    provider: NotBlankStr = Field(
        default="openai",
        description="Embedding provider name",
    )
    model: NotBlankStr = Field(
        default="text-embedding-3-small",
        description="Embedding model identifier",
    )
    dims: int = Field(
        default=1536,
        ge=1,
        description="Embedding vector dimensions",
    )


class Mem0BackendConfig(BaseModel):
    """Mem0-specific backend configuration.

    Attributes:
        data_dir: Directory for Mem0 data persistence.
        collection_name: Qdrant collection name.
        embedder: Embedder settings.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    data_dir: NotBlankStr = Field(
        default="/data/memory",
        description="Directory for Mem0 data persistence",
    )
    collection_name: NotBlankStr = Field(
        default="synthorg_memories",
        description="Qdrant collection name",
    )
    embedder: Mem0EmbedderConfig = Field(
        default_factory=Mem0EmbedderConfig,
        description="Embedder settings",
    )


def build_mem0_config_dict(config: Mem0BackendConfig) -> dict[str, Any]:
    """Build the dict that ``Memory.from_config()`` expects.

    Args:
        config: Mem0 backend configuration.

    Returns:
        Configuration dict suitable for ``Memory.from_config()``.
    """
    return {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": config.collection_name,
                "embedding_model_dims": config.embedder.dims,
                "path": f"{config.data_dir}/qdrant",
            },
        },
        "embedder": {
            "provider": config.embedder.provider,
            "config": {
                "model": config.embedder.model,
            },
        },
        "history_db_path": f"{config.data_dir}/history.db",
        "version": "v1.1",
    }


def build_config_from_company_config(
    config: CompanyMemoryConfig,
) -> Mem0BackendConfig:
    """Derive a ``Mem0BackendConfig`` from the top-level memory config.

    Args:
        config: Company-wide memory configuration.

    Returns:
        Mem0-specific backend configuration.
    """
    return Mem0BackendConfig(
        data_dir=config.storage.data_dir,
    )
