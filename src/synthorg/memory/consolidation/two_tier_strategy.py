"""Two-tier compression consolidation strategy (GEMS).

Compresses raw ``DetailedExperience`` entries into
``CompressedExperience`` instances.  Entries not tagged
``"detailed_experience"`` are left untouched for other strategies.
"""

import asyncio
import builtins
import json
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.consolidation.compressor import (
    ExperienceCompressor,
)
from synthorg.memory.consolidation.config import (
    ExperienceCompressorConfig,
)
from synthorg.memory.consolidation.models import ConsolidationResult
from synthorg.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.consolidation import (
    EXPERIENCE_COMPRESSED,
    TWO_TIER_COMPRESSION_COMPLETE,
    TWO_TIER_COMPRESSION_FAILED,
    TWO_TIER_COMPRESSION_START,
)

logger = get_logger(__name__)

_DETAILED_TAG = "detailed_experience"
_COMPRESSED_TAG = "compressed_experience"
_MAX_CONTEXT_ENTRIES: Final[int] = 5


class TwoTierCompressionStrategy:
    """GEMS two-tier compression strategy.

    Compresses entries tagged ``"detailed_experience"`` into
    ``CompressedExperience`` instances.  The compressed experience
    is stored as a new EPISODIC entry tagged ``"compressed_experience"``.
    The original detailed entry ID is added to ``removed_ids``.

    Entries not tagged ``"detailed_experience"`` are ignored (left
    for other strategies to process).

    Args:
        backend: Memory backend for storing compressed entries.
        compressor: Experience compressor protocol implementation.
        config: Compressor configuration.
    """

    def __init__(
        self,
        *,
        backend: MemoryBackend,
        compressor: ExperienceCompressor,
        config: ExperienceCompressorConfig,
    ) -> None:
        self._backend = backend
        self._compressor = compressor
        self._config = config

    async def consolidate(
        self,
        entries: tuple[MemoryEntry, ...],
        *,
        agent_id: NotBlankStr,
    ) -> ConsolidationResult:
        """Compress detailed experiences into compressed learnings.

        Args:
            entries: Memory entries to process.
            agent_id: Owning agent identifier.

        Returns:
            Result with removed (detailed) and summary (compressed) IDs.
        """
        if not self._config.enabled:
            return ConsolidationResult()

        logger.info(
            TWO_TIER_COMPRESSION_START,
            agent_id=agent_id,
            total_entries=len(entries),
        )
        candidates = [e for e in entries if _DETAILED_TAG in e.metadata.tags]
        if not candidates:
            logger.info(
                TWO_TIER_COMPRESSION_COMPLETE,
                agent_id=agent_id,
                compressed_count=0,
                removed_count=0,
                reason="no_detailed_entries",
            )
            return ConsolidationResult()

        # Fetch context memories for compression
        context_entries = await self._fetch_context(agent_id)

        async def _compress_one(
            entry: MemoryEntry,
        ) -> tuple[str, str] | None:
            """Compress one.

            Returns:
                The resulting ``tuple[str, str]``, or ``None`` when unavailable.

            Raises:
                MemoryError: If the related operation fails.
                RecursionError: If the related operation fails.
            """
            try:
                prompt, output, feedback, trace = self._parse_detailed_content(
                    entry.content
                )
                compressed = await self._compressor.compress(
                    prompt=prompt,
                    output=output,
                    verification_feedback=feedback,
                    reasoning_trace=trace,
                    memory_context=context_entries,
                    agent_id=agent_id,
                    source_artifact_ids=(entry.id,),
                )
                if compressed.compression_ratio < self._config.min_compression_ratio:
                    logger.debug(
                        TWO_TIER_COMPRESSION_FAILED,
                        agent_id=agent_id,
                        entry_id=entry.id,
                        error=(
                            f"compression_ratio {compressed.compression_ratio:.2f} "
                            f"below min {self._config.min_compression_ratio:.2f}"
                        ),
                    )
                    return None
                content = json.dumps(
                    {
                        "strategic_decisions": list(
                            compressed.strategic_decisions,
                        ),
                        "applicable_contexts": list(
                            compressed.applicable_contexts,
                        ),
                        "compression_ratio": compressed.compression_ratio,
                        "compressor_version": compressed.compressor_version,
                        "source_artifact_ids": list(
                            compressed.source_artifact_ids,
                        )
                        or [entry.id],
                    }
                )
                store_request = MemoryStoreRequest(
                    category=MemoryCategory.EPISODIC,
                    content=content,
                    metadata=MemoryMetadata(
                        tags=(_COMPRESSED_TAG,),
                        source=entry.id,
                    ),
                )
                new_id = await self._backend.store(
                    agent_id,
                    store_request,
                )
            except builtins.MemoryError, RecursionError:
                raise
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    TWO_TIER_COMPRESSION_FAILED,
                    agent_id=agent_id,
                    entry_id=entry.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                return None
            else:
                logger.debug(
                    EXPERIENCE_COMPRESSED,
                    agent_id=agent_id,
                    original_id=entry.id,
                    compressed_id=new_id,
                    decisions_count=len(
                        compressed.strategic_decisions,
                    ),
                )
                return (entry.id, new_id)

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_compress_one(c)) for c in candidates]

        removed_ids: list[str] = []
        summary_ids: list[str] = []
        for task in tasks:
            pair = task.result()
            if pair is not None:
                removed_ids.append(pair[0])
                summary_ids.append(pair[1])

        logger.info(
            TWO_TIER_COMPRESSION_COMPLETE,
            agent_id=agent_id,
            compressed_count=len(summary_ids),
            removed_count=len(removed_ids),
        )
        return ConsolidationResult(
            removed_ids=tuple(removed_ids),
            summary_ids=tuple(summary_ids),
        )

    async def _fetch_context(
        self,
        agent_id: str,
    ) -> tuple[MemoryEntry, ...]:
        """Fetch recent entries for compression context.

        Returns:
            Tuple of ``MemoryEntry``.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
        """
        try:
            query = MemoryQuery(limit=_MAX_CONTEXT_ENTRIES)
            return await self._backend.retrieve(agent_id, query)
        except builtins.MemoryError, RecursionError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                TWO_TIER_COMPRESSION_FAILED,
                agent_id=agent_id,
                source="context_fetch",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ()

    def _parse_detailed_content(
        self,
        content: str,
    ) -> tuple[str, str, str | None, tuple[str, ...]]:
        """Parse structured content from a DetailedExperience entry.

        Falls back to using the entire content as the prompt if
        structured parsing fails.

        Returns:
            Tuple of (prompt, output, verification_feedback,
            reasoning_trace).
        """
        try:
            data = json.loads(content)
        except json.JSONDecodeError, TypeError:
            return (content, "", None, ())
        if not isinstance(data, dict):
            return (content, "", None, ())
        prompt = data.get("prompt", content)
        output = data.get("output", "")
        feedback = data.get("verification_feedback")
        trace = data.get("reasoning_trace", ())
        if not isinstance(prompt, str) or not isinstance(output, str):
            return (content, "", None, ())
        if feedback is not None and not isinstance(feedback, str):
            return (content, "", None, ())
        if not isinstance(trace, list | tuple) or not all(
            isinstance(step, str) for step in trace
        ):
            return (content, "", None, ())
        return (prompt, output, feedback, tuple(trace))
