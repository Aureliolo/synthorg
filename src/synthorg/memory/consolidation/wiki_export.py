"""Post-consolidation markdown wiki filesystem export.

Serializes consolidated org memory and compressed experiences to a
three-view filesystem tree: ``raw/``, ``wiki/``, and ``index.md``.
"""

import asyncio
import builtins
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.memory.models import MemoryQuery
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.consolidation import (
    WIKI_EXPORT_COMPLETE,
    WIKI_EXPORT_FAILED,
    WIKI_EXPORT_START,
)

if TYPE_CHECKING:
    from synthorg.memory.consolidation.config import WikiExportConfig
    from synthorg.memory.protocol import MemoryBackend

logger = get_logger(__name__)

_DETAILED_TAG = "detailed_experience"
_COMPRESSED_TAG = "compressed_experience"


class WikiExportResult(BaseModel):
    """Result of a wiki export operation.

    Attributes:
        raw_count: Number of raw entries exported.
        compressed_count: Number of compressed entries exported.
        export_root: Root directory of the export.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    raw_count: int = Field(default=0, ge=0)
    compressed_count: int = Field(default=0, ge=0)
    export_root: NotBlankStr


class WikiExporter:
    """Exports memory tiers as a three-view wiki filesystem.

    Structure::

        <export_root>/
          raw/
            <artifact_id>.md    # Tier 1 raw artifacts
          wiki/
            <compressed_id>.md  # Tier 2 compressed experiences
          index.md              # Auto-generated navigation

    Args:
        backend: Memory backend for querying entries.
        config: Wiki export configuration.
    """

    def __init__(
        self,
        *,
        backend: MemoryBackend,
        config: WikiExportConfig,
    ) -> None:
        self._backend = backend
        self._config = config

    async def export(
        self,
        agent_id: NotBlankStr,
    ) -> WikiExportResult:
        """Export memory tiers to the filesystem.

        Args:
            agent_id: Agent whose memories to export.

        Returns:
            Export result with counts and root path.
        """
        if not self._config.enabled:
            return WikiExportResult(
                raw_count=0,
                compressed_count=0,
                export_root=self._config.export_root,
            )

        logger.info(
            WIKI_EXPORT_START,
            agent_id=agent_id,
            export_root=self._config.export_root,
        )
        root = Path(self._config.export_root)
        raw_dir = root / "raw"
        wiki_dir = root / "wiki"
        try:
            raw_dir.mkdir(parents=True, exist_ok=True)
            wiki_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                WIKI_EXPORT_FAILED,
                tier="setup",
                export_root=self._config.export_root,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return WikiExportResult(
                raw_count=0,
                compressed_count=0,
                export_root=self._config.export_root,
            )

        raw_count = 0
        compressed_count = 0

        if self._config.include_raw_tier:
            raw_count = await self._export_raw(
                agent_id,
                raw_dir,
            )

        if self._config.include_compressed_tier:
            compressed_count = await self._export_compressed(
                agent_id,
                wiki_dir,
            )

        self._write_index(root, raw_count, compressed_count)

        logger.info(
            WIKI_EXPORT_COMPLETE,
            agent_id=agent_id,
            raw_count=raw_count,
            compressed_count=compressed_count,
        )
        return WikiExportResult(
            raw_count=raw_count,
            compressed_count=compressed_count,
            export_root=self._config.export_root,
        )

    async def _export_raw(
        self,
        agent_id: str,
        output_dir: Path,
    ) -> int:
        """Export Tier 1 raw artifacts to ``raw/`` directory."""
        query = MemoryQuery(
            tags=(_DETAILED_TAG,),
            # ``MemoryQuery.limit`` is capped at 1000 by schema;
            # ``None`` in the config means "use the backend max".
            limit=self._config.max_entries_per_view or 1000,
        )
        try:
            entries = await self._backend.retrieve(agent_id, query)
        except builtins.MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                WIKI_EXPORT_FAILED,
                tier="raw",
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return 0

        exported = 0
        for entry in entries:
            md_content = (
                f"---\n"
                f"id: {entry.id}\n"
                f"agent_id: {entry.agent_id}\n"
                f"category: {entry.category.value}\n"
                f"created_at: {entry.created_at.isoformat()}\n"
                f"---\n\n"
                f"{entry.content}\n"
            )
            safe_name = entry.id.replace("/", "_").replace("\\", "_")
            filepath = output_dir / f"{safe_name}.md"
            resolved = filepath.resolve()
            if not resolved.is_relative_to(output_dir.resolve()):  # noqa: ASYNC240
                logger.warning(
                    WIKI_EXPORT_FAILED,
                    tier="raw",
                    entry_id=entry.id,
                    error="path traversal detected",
                )
                continue
            try:
                await asyncio.to_thread(
                    filepath.write_text,
                    md_content,
                    encoding="utf-8",
                )
            except builtins.MemoryError, RecursionError:
                raise
            except OSError as exc:
                logger.warning(
                    WIKI_EXPORT_FAILED,
                    tier="raw",
                    entry_id=entry.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                continue
            exported += 1

        return exported

    async def _export_compressed(
        self,
        agent_id: str,
        output_dir: Path,
    ) -> int:
        """Export Tier 2 compressed experiences to ``wiki/`` directory."""
        query = MemoryQuery(
            tags=(_COMPRESSED_TAG,),
            # ``MemoryQuery.limit`` is capped at 1000 by schema;
            # ``None`` in the config means "use the backend max".
            limit=self._config.max_entries_per_view or 1000,
        )
        try:
            entries = await self._backend.retrieve(agent_id, query)
        except builtins.MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                WIKI_EXPORT_FAILED,
                tier="compressed",
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return 0

        exported = 0
        for entry in entries:
            # Parse structured content for rich markdown
            try:
                data = json.loads(entry.content)
                if not isinstance(data, dict):
                    msg = f"expected dict, got {type(data).__name__}"
                    raise TypeError(msg)  # noqa: TRY301
                raw_decisions = data.get("strategic_decisions", [])
                raw_contexts = data.get("applicable_contexts", [])
                decisions = (
                    [d for d in raw_decisions if isinstance(d, str)]
                    if isinstance(raw_decisions, list)
                    else []
                )
                contexts = (
                    [c for c in raw_contexts if isinstance(c, str)]
                    if isinstance(raw_contexts, list)
                    else []
                )
            except json.JSONDecodeError, TypeError:
                logger.warning(
                    WIKI_EXPORT_FAILED,
                    tier="compressed_parse",
                    entry_id=entry.id,
                )
                decisions = []
                contexts = []

            decisions_md = (
                "\n".join(f"- {d}" for d in decisions)
                if decisions
                else "_No decisions recorded._"
            )
            contexts_md = (
                "\n".join(f"- {c}" for c in contexts)
                if contexts
                else "_No contexts recorded._"
            )

            md_content = (
                f"---\n"
                f"id: {entry.id}\n"
                f"agent_id: {entry.agent_id}\n"
                f"category: {entry.category.value}\n"
                f"created_at: {entry.created_at.isoformat()}\n"
                f"---\n\n"
                f"## Strategic Decisions\n\n"
                f"{decisions_md}\n\n"
                f"## Applicable Contexts\n\n"
                f"{contexts_md}\n"
            )
            safe_name = entry.id.replace("/", "_").replace("\\", "_")
            filepath = output_dir / f"{safe_name}.md"
            resolved = filepath.resolve()
            if not resolved.is_relative_to(output_dir.resolve()):  # noqa: ASYNC240
                logger.warning(
                    WIKI_EXPORT_FAILED,
                    tier="compressed",
                    entry_id=entry.id,
                    error="path traversal detected",
                )
                continue
            try:
                await asyncio.to_thread(
                    filepath.write_text,
                    md_content,
                    encoding="utf-8",
                )
            except builtins.MemoryError, RecursionError:
                raise
            except OSError as exc:
                logger.warning(
                    WIKI_EXPORT_FAILED,
                    tier="compressed",
                    entry_id=entry.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                continue
            exported += 1

        return exported

    def _write_index(
        self,
        root: Path,
        raw_count: int,
        compressed_count: int,
    ) -> None:
        """Generate ``index.md`` with navigation links."""
        now = datetime.now(UTC).isoformat()
        index_content = (
            f"# Memory Wiki Export\n\n"
            f"**Exported at:** {now}\n\n"
            f"## Summary\n\n"
            f"- Raw artifacts: {raw_count}\n"
            f"- Compressed experiences: {compressed_count}\n\n"
            f"## Views\n\n"
            f"- [Raw Artifacts](raw/) -- Tier 1 execution traces\n"
            f"- [Compressed Experiences](wiki/) -- "
            f"Tier 2 strategic learnings\n"
        )
        index_path = root / "index.md"
        try:
            index_path.write_text(index_content, encoding="utf-8")
        except builtins.MemoryError, RecursionError:
            raise
        except OSError as exc:
            logger.warning(
                WIKI_EXPORT_FAILED,
                tier="index",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
