"""Repository source loader.

Walks a local repository tree (``source.uri`` is the root path) in
deterministic order and emits one ``CODE`` :class:`RawUnit` per text
file, each with a :class:`CodeLocator` (repo-relative path + line span).
Binary, oversized, vendored, and VCS-internal files are skipped so the
corpus stays meaningful and chunk ids stay stable across re-ingests.
"""

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Final

from synthorg.core.types import NotBlankStr
from synthorg.knowledge.enums import ContentKind
from synthorg.knowledge.errors import KnowledgeSourceUnavailableError
from synthorg.knowledge.models import CodeLocator, RawDocument, RawUnit
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.knowledge import (
    KNOWLEDGE_SOURCE_FILE_SKIPPED,
    KNOWLEDGE_SOURCE_LOADED,
)
from synthorg.versioning.hashing import compute_text_hash

if TYPE_CHECKING:
    from synthorg.knowledge.models import KnowledgeSource

logger = get_logger(__name__)

_MAX_FILE_BYTES: Final[int] = 1_000_000
"""Skip files larger than this; oversized blobs are rarely useful corpus."""

_IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "dist",
        "build",
        ".next",
        "target",
        "vendor",
    }
)

_TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".go",
        ".rs",
        ".java",
        ".rb",
        ".c",
        ".h",
        ".cpp",
        ".cc",
        ".hpp",
        ".cs",
        ".php",
        ".kt",
        ".scala",
        ".swift",
        ".md",
        ".rst",
        ".txt",
        ".toml",
        ".yaml",
        ".yml",
        ".json",
        ".cfg",
        ".ini",
        ".sql",
        ".sh",
    }
)


class RepoLoader:
    """Loads a local repository tree into per-file code units."""

    __slots__ = ("_max_file_bytes",)

    def __init__(self, *, max_file_bytes: int = _MAX_FILE_BYTES) -> None:
        self._max_file_bytes = max_file_bytes

    async def load(self, source: KnowledgeSource) -> RawDocument:
        """Walk ``source.uri`` and emit one unit per eligible text file.

        Returns:
            A ``RawDocument`` with one unit per eligible text file under
            the source path.
        """
        document = await asyncio.to_thread(self._load_sync, source)
        logger.debug(
            KNOWLEDGE_SOURCE_LOADED,
            source_id=source.source_id,
            source_type=source.source_type.value,
            unit_count=len(document.units),
        )
        return document

    def _load_sync(self, source: KnowledgeSource) -> RawDocument:
        root = Path(source.uri)
        if not root.is_dir():
            msg = f"Repository path is not a directory: {source.uri!r}"
            raise KnowledgeSourceUnavailableError(msg)
        units: list[RawUnit] = []
        hash_parts: list[str] = []
        # os.walk with in-place ``dirnames`` pruning skips entire
        # ignored subtrees (.git, node_modules, .venv, ...) without
        # stat-ing every descendant; rglob("*") otherwise traverses the
        # full tree before the per-path filter runs.
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d not in _IGNORED_DIRS)
            for filename in sorted(filenames):
                path = Path(dirpath) / filename
                if not self._is_eligible(path, root=root):
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError) as exc:
                    # Emit a trail so operators can see why files are
                    # absent from a corpus instead of silently dropping them.
                    logger.debug(
                        KNOWLEDGE_SOURCE_FILE_SKIPPED,
                        source_id=source.source_id,
                        file_path=str(path),
                        reason="read_failed",
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                    continue
                rel = path.relative_to(root).as_posix()
                units.append(
                    RawUnit(
                        text=text,
                        locator=CodeLocator(
                            path=NotBlankStr(rel),
                            line_start=1,
                            line_end=max(1, text.count("\n") + 1),
                        ),
                        content_kind=ContentKind.CODE,
                    )
                )
                hash_parts.append(f"{rel}\n{text}")
        return RawDocument(
            source_id=source.source_id,
            source_type=source.source_type,
            uri=source.uri,
            title=NotBlankStr(source.title),
            content_hash=compute_text_hash("\n".join(hash_parts)),
            units=tuple(units),
        )

    def _is_eligible(self, path: Path, *, root: Path) -> bool:
        # Symlinks are skipped before any file probe: a symlink that
        # resolves outside ``source.uri`` would otherwise let a hostile
        # repo coerce the loader into reading arbitrary host files.
        if path.is_symlink():
            return False
        if not path.is_file():
            return False
        # Filter on path components RELATIVE to the repository root: an
        # absolute ``path.parts`` would include ancestor segments above
        # the root and could accidentally drop valid files when the
        # caller's parent dir happens to share a name with an ignored
        # directory (e.g. a project living under ``.../vendor/``).
        rel_parts = path.relative_to(root).parts
        if any(part in _IGNORED_DIRS for part in rel_parts):
            return False
        if path.suffix.lower() not in _TEXT_EXTENSIONS:
            return False
        try:
            return path.stat().st_size <= self._max_file_bytes
        except OSError:
            return False
