"""Memory consolidation configuration models.

Frozen Pydantic models for consolidation interval, retention,
archival, LLM consolidation strategy, experience compressor,
and wiki export settings.
"""

from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import ClassVar, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.consolidation.models import RetentionRule
from synthorg.memory.enums import ConsolidationInterval
from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import (
    MirrorField,
    apply_settings_mirrors,
    parse_bool,
)

logger = get_logger(__name__)


class ConsolidationStrategyType(StrEnum):
    """Discriminator selecting the consolidation composite to build.

    Each value maps (in
    :mod:`synthorg.memory.consolidation.factory`) to a
    ``Composite(HighestRelevanceSelector, <op>)``:

    - ``SIMPLE`` -> ``ConcatenationOp`` (truncated-bullet summary).
    - ``DUAL_MODE`` -> ``DensityRoutingOp`` (density-routed
      extractive / abstractive).
    - ``LLM`` -> ``LLMSynthesisOp`` (LLM synthesis, parallel groups).
    """

    SIMPLE = "simple"
    DUAL_MODE = "dual_mode"
    LLM = "llm"


class RetentionConfig(BaseModel):
    """Per-category retention configuration (company-level defaults).

    These rules apply as the baseline for all agents.  Individual agents
    can override specific categories via
    :attr:`~synthorg.core.agent.MemoryConfig.retention_overrides`.

    Resolution order per category (highest priority first):

    1. Agent per-category override
    2. Company per-category rule (this config)
    3. Agent global default (``MemoryConfig.retention_days``)
    4. Company global default (``default_retention_days``)
    5. Keep forever (no expiry)

    Attributes:
        rules: Per-category retention rules (unique categories).
        default_retention_days: Default retention in days
            (``None`` = keep forever).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    rules: tuple[RetentionRule, ...] = Field(
        default=(),
        description="Per-category retention rules",
    )
    default_retention_days: int | None = Field(
        default=None,
        ge=1,
        description="Default retention in days (None = forever)",
    )

    @model_validator(mode="after")
    def _validate_unique_categories(self) -> Self:
        """Ensure each category appears at most once in rules.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        categories = [rule.category for rule in self.rules]
        if len(categories) != len(set(categories)):
            seen: set[MemoryCategory] = set()
            dupes: set[str] = set()
            for c in categories:
                if c in seen:
                    dupes.add(c.value)
                seen.add(c)
            sorted_dupes = sorted(dupes)
            msg = f"Duplicate retention categories: {sorted_dupes}"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="RetentionConfig",
                field="rules",
                duplicates=sorted_dupes,
                reason=msg,
            )
            raise ValueError(msg)
        return self


class DualModeConfig(BaseModel):
    """Configuration for dual-mode archival.

    Controls density-aware archival: LLM abstractive summaries for
    sparse/conversational content vs extractive preservation (verbatim
    key facts + start/mid/end anchors) for dense/factual content.

    Attributes:
        enabled: Whether dual-mode density classification is active.
            When ``False``, the dual-mode strategy is not used.
        dense_threshold: Density score threshold for DENSE classification
            (0.0 = classify everything as dense, 1.0 = everything sparse).
        summarization_model: Model ID for abstractive summarization.
        max_summary_tokens: Maximum tokens for LLM summary responses.
        max_facts: Maximum number of extracted key facts for extractive
            mode.
        anchor_length: Character length for each extractive anchor
            snippet (start/mid/end).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether dual-mode density classification is active",
    )
    dense_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Density score threshold for DENSE classification",
    )
    summarization_model: NotBlankStr | None = Field(
        default=None,
        description="Model ID for abstractive summarization",
    )
    max_summary_tokens: int = Field(
        default=200,
        ge=50,
        le=1000,
        description="Maximum tokens for LLM summary responses",
    )
    max_facts: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum extracted key facts for extractive mode",
    )
    anchor_length: int = Field(
        default=150,
        ge=50,
        le=500,
        description="Character length for each extractive anchor",
    )

    @model_validator(mode="after")
    def _validate_model_when_enabled(self) -> Self:
        """Require a summarization model when dual-mode is enabled.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.enabled and self.summarization_model is None:
            msg = "summarization_model must be non-blank when dual-mode is enabled"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="DualModeConfig",
                field="summarization_model",
                enabled=self.enabled,
                reason=msg,
            )
            raise ValueError(msg)
        return self


class ArchivalConfig(BaseModel):
    """Archival configuration.

    Attributes:
        enabled: Whether archival is enabled.
        age_threshold_days: Minimum age in days before archival.
        dual_mode: Dual-mode archival configuration.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether archival is enabled",
    )
    age_threshold_days: int = Field(
        default=90,
        ge=1,
        description="Minimum age in days before archival",
    )
    dual_mode: DualModeConfig = Field(
        default_factory=DualModeConfig,
        description="Dual-mode archival configuration",
    )


class ExperienceCompressorConfig(BaseModel):
    """Configuration for the GEMS two-tier experience compressor.

    Controls whether raw execution traces (``DetailedExperience``) are
    compressed into strategic learnings (``CompressedExperience``).

    Attributes:
        enabled: Whether two-tier compression is active.
        model: Model identifier for the compressor LLM call
            (``None`` = use medium-tier default).
        temperature: Sampling temperature for compression.
        max_tokens: Token budget for the compressor response.
        min_compression_ratio: Discard compressions with a ratio below
            this threshold (0.0 = keep all, closer to 1.0 = stricter).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether two-tier compression is active",
    )
    model: NotBlankStr | None = Field(
        default=None,
        description=(
            "Model identifier for the compressor LLM call "
            "(None = use medium-tier default)"
        ),
    )
    temperature: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Sampling temperature for compression",
    )
    max_tokens: int = Field(
        default=1000,
        ge=100,
        le=4000,
        description="Token budget for the compressor response",
    )
    min_compression_ratio: float = Field(
        default=0.0,
        ge=0.0,
        lt=1.0,
        description=(
            "Discard compressions with a ratio below this threshold (0.0 = keep all)"
        ),
    )


class WikiExportConfig(BaseModel):
    """Configuration for post-consolidation wiki filesystem export.

    Three-view export: ``raw/`` (Tier 1 raw artifacts), ``wiki/``
    (Tier 2 compressed experiences), and ``index.md`` (navigation).

    Attributes:
        enabled: Whether wiki export is enabled.
        export_root: Root directory for the wiki filesystem export.
        trigger: When to trigger export (``"on_consolidation"`` or
            ``"manual"``).
        include_raw_tier: Export Tier 1 (DetailedExperience) to
            ``raw/`` view.
        include_compressed_tier: Export Tier 2 (CompressedExperience)
            to ``wiki/`` view.
        max_entries_per_view: Maximum entries per view (``None`` = all).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether wiki export is enabled",
    )
    export_root: NotBlankStr = Field(
        default="/data/wiki",
        description="Root directory for the wiki filesystem export",
    )
    trigger: Literal["on_consolidation", "manual"] = Field(
        default="manual",
        description="When to trigger export",
    )
    include_raw_tier: bool = Field(
        default=True,
        description="Export Tier 1 raw artifacts to raw/ view",
    )
    include_compressed_tier: bool = Field(
        default=True,
        description="Export Tier 2 compressed experiences to wiki/ view",
    )
    max_entries_per_view: int | None = Field(
        default=None,
        ge=1,
        le=1000,
        description=(
            "Maximum entries per view.  ``None`` means 'use the "
            "backend's maximum page size' (``MemoryQuery.limit`` is "
            "capped at 1000 by schema).  Multi-page exports for "
            "collections larger than 1000 are not yet supported."
        ),
    )

    @model_validator(mode="after")
    def _reject_traversal(self) -> Self:
        """Reject parent-directory traversal in export_root.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        parts = (
            PureWindowsPath(self.export_root).parts
            + PurePosixPath(self.export_root).parts
        )
        if ".." in parts:
            msg = "export_root must not contain parent-directory traversal (..)"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="WikiExportConfig",
                field="export_root",
                value=self.export_root,
                reason=msg,
            )
            raise ValueError(msg)
        return self


class ConsolidationConfig(BaseModel):
    """Top-level memory consolidation configuration.

    Attributes:
        enabled: Master kill switch for memory consolidation. When
            ``False`` the consolidation scheduler is constructed but
            every tick short-circuits -- operator-safe way to pause
            consolidation without tearing down lifecycle plumbing.
        interval: How often to run consolidation.
        max_memories_per_agent: Upper bound on memories per agent.
        retention: Per-category retention settings.
        archival: Archival settings.
        experience_compressor: GEMS two-tier compressor settings.
        wiki_export: Wiki filesystem export settings.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="enabled",
            namespace=SettingNamespace.MEMORY,
            key="consolidation_enabled",
            parse=parse_bool,
        ),
    )

    enabled: bool = Field(
        default=True,
        description=(
            "Master kill switch for memory consolidation. When False"
            " every consolidation tick short-circuits immediately."
        ),
    )
    interval: ConsolidationInterval = Field(
        default=ConsolidationInterval.DAILY,
        description="How often to run consolidation",
    )
    max_memories_per_agent: int = Field(
        default=10_000,
        ge=1,
        description="Upper bound on memories per agent",
    )
    retention: RetentionConfig = Field(
        default_factory=RetentionConfig,
        description="Per-category retention settings",
    )
    archival: ArchivalConfig = Field(
        default_factory=ArchivalConfig,
        description="Archival settings",
    )
    experience_compressor: ExperienceCompressorConfig = Field(
        default_factory=ExperienceCompressorConfig,
        description="GEMS two-tier experience compressor settings",
    )
    wiki_export: WikiExportConfig = Field(
        default_factory=WikiExportConfig,
        description="Wiki filesystem export settings",
    )

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: object) -> object:
        """Apply mirrors.

        Returns:
            The input data with any unset mirror fields populated.
        """
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)


_MIN_LLM_GROUP_THRESHOLD: Final[int] = 3


class LLMConsolidationConfig(BaseModel):
    """Configuration for the LLM-based consolidation strategy.

    Encapsulates the LLM synthesis tuning knobs (group/density
    thresholds, prompt caps, retry budget). Aligns with the frozen
    Pydantic config convention used by sibling configs
    (``DualModeConfig``, ``RetentionConfig``).

    Attributes:
        group_threshold: Minimum category group size for consolidation.
            At threshold 3, ``_select_entries`` keeps one entry and
            ``_synthesize`` receives two -- the smallest input for a
            meaningful LLM merge.
        temperature: Sampling temperature for the synthesis LLM call.
        top_p: Nucleus-sampling cap for the synthesis LLM call. Pinned to
            ``1.0`` by default so determinism rests on ``temperature``.
        max_summary_tokens: Maximum tokens for the synthesis response.
        include_distillation_context: When True, fetches recent
            distillation entries as trajectory context for the
            synthesis prompt.
        max_trajectory_context_entries: Maximum distillation entries
            to include as trajectory context.
        max_trajectory_chars_per_entry: Character limit per trajectory
            snippet in the synthesis prompt.
        max_entry_input_chars: Per-entry content character limit before
            being sent to the LLM.
        max_total_user_content_chars: Total character cap for the
            concatenated user prompt sent to the LLM.
        fallback_truncate_length: Per-entry truncation limit in
            concatenation-fallback summaries.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    group_threshold: int = Field(
        default=3,
        ge=_MIN_LLM_GROUP_THRESHOLD,
        description=("Minimum category group size for consolidation (must be >= 3)"),
    )
    temperature: float = Field(
        default=0.3,
        ge=0.0,
        le=2.0,
        description="Sampling temperature for the synthesis LLM call",
    )
    top_p: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Nucleus-sampling cap for the synthesis LLM call",
    )
    max_summary_tokens: int = Field(
        default=500,
        ge=50,
        description="Maximum tokens for the synthesis response",
    )
    include_distillation_context: bool = Field(
        default=True,
        description=(
            "When True, fetch recent distillation entries as "
            "trajectory context for the synthesis prompt"
        ),
    )
    max_trajectory_context_entries: int = Field(
        default=5,
        ge=1,
        description=("Maximum distillation entries to include as trajectory context"),
    )
    max_trajectory_chars_per_entry: int = Field(
        default=500,
        ge=50,
        description=("Character limit per trajectory snippet in the synthesis prompt"),
    )
    max_entry_input_chars: int = Field(
        default=2000,
        ge=100,
        description=("Per-entry content character limit before being sent to the LLM"),
    )
    max_total_user_content_chars: int = Field(
        default=20000,
        ge=1000,
        description=(
            "Total character cap for the concatenated user prompt sent to the LLM"
        ),
    )
    fallback_truncate_length: int = Field(
        default=200,
        ge=50,
        description=("Per-entry truncation limit in concatenation-fallback summaries"),
    )

    @model_validator(mode="after")
    def _validate_entry_vs_total_chars(self) -> Self:
        """Ensure per-entry cap does not exceed total prompt cap.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.max_entry_input_chars > self.max_total_user_content_chars:
            msg = (
                f"max_entry_input_chars ({self.max_entry_input_chars}) "
                f"must not exceed max_total_user_content_chars "
                f"({self.max_total_user_content_chars})"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="LLMConsolidationConfig",
                field="max_entry_input_chars",
                max_entry_input_chars=self.max_entry_input_chars,
                max_total_user_content_chars=self.max_total_user_content_chars,
                reason=msg,
            )
            raise ValueError(msg)
        return self
