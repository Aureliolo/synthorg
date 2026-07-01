"""Configuration for the ollama VRAM-aware model load/eviction guard.

Kept import-light (pydantic only) because the config schema imports it
at cold-import time; the runtime guard lives in
:mod:`synthorg.providers.ollama_vram_guard`.
"""

from pydantic import BaseModel, ConfigDict, Field


class OllamaVramGuardConfig(BaseModel):
    """VRAM guard behaviour for one ollama provider (host).

    The guard runs before dispatching a completion to a model that is
    not already resident in GPU memory. When loading the target would
    spill it (or an already-loaded model) to CPU, the guard first
    unloads the least-recently-used loaded model; when everything fits
    fully on the GPU, loaded models are left alone.

    Attributes:
        enabled: Master switch for the guard.
        total_vram_mb: Total GPU memory available to ollama, in MiB.
            When positive, the guard predicts whether the target model
            fits before loading and evicts pre-emptively. When 0 the
            guard runs in reactive mode: it evicts only when the
            ollama runtime already reports a loaded model spilled to
            CPU (``size_vram < size``).
        headroom_fraction: Fraction of ``total_vram_mb`` the guard is
            allowed to plan against; the remainder is headroom for KV
            cache growth and other consumers.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Master switch for the ollama VRAM guard.",
    )
    total_vram_mb: int = Field(
        default=0,
        ge=0,
        description=(
            "Total GPU memory available to ollama in MiB (0 = reactive "
            "mode: evict only on an observed CPU spill)."
        ),
    )
    headroom_fraction: float = Field(
        default=0.9,
        gt=0.0,
        le=1.0,
        description=(
            "Fraction of total_vram_mb the guard plans against; the rest "
            "is headroom for KV-cache growth."
        ),
    )
