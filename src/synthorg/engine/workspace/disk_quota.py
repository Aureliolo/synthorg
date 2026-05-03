"""Disk quota watcher for worktree isolation.

Monitors per-worktree disk usage and emits warning/exceeded events
when thresholds are crossed.  Actual worktree removal is delegated
to the workspace manager -- this module only signals.
"""

import asyncio
from pathlib import Path  # noqa: TC003
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    WORKSPACE_DISK_CHECK_ERROR,
    WORKSPACE_DISK_EXCEEDED,
    WORKSPACE_DISK_TRAVERSAL_ERROR,
    WORKSPACE_DISK_WARNING,
)

from .config import PlannerWorktreesConfig  # noqa: TC001

logger = get_logger(__name__)

_BYTES_PER_GB = 1_073_741_824


class DiskQuotaStatus(BaseModel):
    """Status of a single worktree's disk usage.

    Attributes:
        path: Worktree directory path.
        usage_gb: Current disk usage in GB.
        limit_gb: Configured maximum in GB.
        status: One of ``ok``, ``warning``, or ``exceeded``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    path: Path = Field(description="Worktree directory path")
    usage_gb: float = Field(ge=0.0, description="Current usage in GB")
    limit_gb: float = Field(gt=0.0, description="Configured limit in GB")
    status: Literal["ok", "warning", "exceeded", "error"] = Field(
        description="Quota status",
    )


def _compute_dir_size_bytes(path: Path) -> int:
    """Recursively compute directory size in bytes.

    Returns 0 for non-existent or inaccessible directories.
    """
    if not path.exists() or not path.is_dir():
        return 0
    total = 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_symlink():
                    continue
                if entry.is_file():
                    total += entry.stat().st_size
            except OSError:
                logger.debug(
                    WORKSPACE_DISK_TRAVERSAL_ERROR,
                    path=str(entry),
                )
                continue
    except OSError:
        logger.debug(
            WORKSPACE_DISK_TRAVERSAL_ERROR,
            path=str(path),
        )
    return total


class DiskQuotaWatcher:
    """Monitor worktree disk usage against configured limits.

    Emits observability events when usage crosses warning or exceeded
    thresholds.  Does not perform cleanup itself -- signals via the
    returned status for the workspace manager to act on.

    Args:
        config: Planner worktrees configuration with disk quota fields.
    """

    def __init__(self, config: PlannerWorktreesConfig) -> None:
        self._config = config

    async def check_worktree(self, path: Path) -> DiskQuotaStatus:
        """Check a single worktree's disk usage.

        Args:
            path: Worktree directory path.

        Returns:
            Status with usage, limit, and ok/warning/exceeded.
        """
        size_bytes = await asyncio.to_thread(_compute_dir_size_bytes, path)
        usage_gb = size_bytes / _BYTES_PER_GB
        limit_gb = self._config.max_disk_gb_per_worktree
        warning_gb = limit_gb * self._config.cleanup_warning_threshold

        if usage_gb >= limit_gb:
            status: Literal["ok", "warning", "exceeded", "error"] = "exceeded"
            logger.warning(
                WORKSPACE_DISK_EXCEEDED,
                path=str(path),
                usage_gb=usage_gb,
                limit_gb=limit_gb,
                auto_cleanup=self._config.auto_cleanup_on_threshold,
            )
        elif usage_gb >= warning_gb:
            status = "warning"
            logger.warning(
                WORKSPACE_DISK_WARNING,
                path=str(path),
                usage_gb=usage_gb,
                limit_gb=limit_gb,
                threshold=self._config.cleanup_warning_threshold,
            )
        else:
            status = "ok"

        return DiskQuotaStatus(
            path=path,
            usage_gb=usage_gb,
            limit_gb=limit_gb,
            status=status,
        )

    async def run_monitor_cycle(
        self,
        worktree_paths: tuple[Path, ...],
    ) -> tuple[DiskQuotaStatus, ...]:
        """Check all worktree paths in a single monitor cycle.

        Args:
            worktree_paths: Paths to check.

        Returns:
            Tuple of statuses for each path.
        """

        async def _safe_check(p: Path) -> DiskQuotaStatus:
            try:
                return await self.check_worktree(p)
            except MemoryError, RecursionError:
                raise
            except Exception:
                logger.warning(
                    WORKSPACE_DISK_CHECK_ERROR,
                    path=str(p),
                    exc_info=True,
                )
                return DiskQuotaStatus(
                    path=p,
                    usage_gb=0.0,
                    limit_gb=self._config.max_disk_gb_per_worktree,
                    status="error",
                )

        statuses: list[DiskQuotaStatus] = [
            DiskQuotaStatus(
                path=p,
                usage_gb=0.0,
                limit_gb=self._config.max_disk_gb_per_worktree,
                status="ok",
            )
            for p in worktree_paths
        ]
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_safe_check(p)) for p in worktree_paths]
        statuses = [t.result() for t in tasks]
        return tuple(statuses)
