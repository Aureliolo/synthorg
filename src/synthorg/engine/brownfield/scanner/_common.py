"""Shared deterministic file-tree helpers for structure-map scanners.

Scanners are pure and synchronous; the aggregator off-loads the whole
scan to a worker thread. These helpers prune VCS/vendor/build dirs and
walk in sorted order so a scan of the same tree is byte-stable (which the
content-hash short-circuit on re-import relies on).
"""

import os
from pathlib import Path
from typing import Final

from synthorg.observability import get_logger
from synthorg.observability.events.brownfield import BROWNFIELD_STRUCTURE_SCANNED

logger = get_logger(__name__)

IGNORED_DIRS: Final[frozenset[str]] = frozenset(
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
        ".tox",
        ".idea",
        ".vscode",
    }
)

_MAX_WALK_ENTRIES: Final[int] = 50_000
"""Cap on visited paths so a pathological tree cannot stall a scan."""


def walk_relative_paths(root: Path) -> list[str]:
    """Return repository-relative POSIX paths under *root*, sorted.

    Prunes :data:`IGNORED_DIRS`. Bounded by :data:`_MAX_WALK_ENTRIES`.
    """
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
        base = Path(dirpath)
        for filename in sorted(filenames):
            rel = (base / filename).relative_to(root).as_posix()
            found.append(rel)
            if len(found) >= _MAX_WALK_ENTRIES:
                # Truncated: the scan (and its content hash) is now partial,
                # so a re-scan whose boundary shifts is not guaranteed stable.
                # Surface it rather than silently dropping modules/deps.
                logger.warning(
                    BROWNFIELD_STRUCTURE_SCANNED,
                    root=str(root),
                    truncated=True,
                    max_entries=_MAX_WALK_ENTRIES,
                )
                return found
    return found


def top_level_dirs(root: Path) -> list[str]:
    """Return immediate subdirectory names of *root* (sorted, pruned)."""
    if not root.is_dir():
        return []
    return sorted(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and entry.name not in IGNORED_DIRS
    )


def read_text_if_present(path: Path) -> str | None:
    """Return file text, or ``None`` if absent / unreadable."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
