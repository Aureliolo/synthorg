"""Kanban board WIP limits, configuration, and enforcement.

Provides the ``KanbanConfig`` model with per-column WIP (Work In Progress)
limits and the ``check_wip_limit`` function for enforcement.
"""

from collections import Counter
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.engine.workflow.kanban_columns import KanbanColumn
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    KANBAN_CONFIG_VALIDATION_FAILED,
    KANBAN_WIP_LIMIT_EXCEEDED,
    KANBAN_WIP_LIMIT_REACHED,
)

logger = get_logger(__name__)


class KanbanWipLimit(BaseModel):
    """WIP limit for a single Kanban column.

    Attributes:
        column: The column this limit applies to.
        limit: Maximum number of tasks allowed in the column.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    column: KanbanColumn = Field(description="Target column")
    limit: int = Field(
        ge=1,
        le=100,
        description="Maximum tasks in the column",
    )


class WipCheckResult(BaseModel):
    """Result of a WIP limit check.

    Attributes:
        allowed: Whether the move is allowed.
        column: The column that was checked.
        current_count: Current task count in the column.
        limit: Configured limit (``None`` if no limit set).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    allowed: bool = Field(description="Whether the move is allowed")
    column: KanbanColumn = Field(description="Checked column")
    current_count: int = Field(
        ge=0,
        description="Current task count in the column",
    )
    limit: int | None = Field(
        default=None,
        description="Configured limit (None = unlimited)",
    )


class KanbanConfig(BaseModel):
    """Kanban board configuration with WIP limits.

    Attributes:
        wip_limits: Per-column WIP limits.  Columns without an entry
            have no limit.
        enforce_wip: When ``True``, WIP violations are hard-rejected
            (check returns ``allowed=False``).  When ``False``, violations
            are advisory-only (logged as warnings but ``allowed=True``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    wip_limits: tuple[KanbanWipLimit, ...] = Field(
        default=(
            KanbanWipLimit(column=KanbanColumn.IN_PROGRESS, limit=5),
            KanbanWipLimit(column=KanbanColumn.REVIEW, limit=3),
        ),
        description="Per-column WIP limits",
    )
    enforce_wip: bool = Field(
        default=True,
        description=(
            "True = hard reject on WIP violation, False = advisory (log warning)"
        ),
    )

    @model_validator(mode="after")
    def _validate_no_duplicate_columns(self) -> Self:
        """Reject duplicate column entries in wip_limits.

        Returns:
            ``self`` unchanged when every WIP-limit column is unique.

        Raises:
            ValueError: When two WIP-limit entries target the same
                column.
        """
        columns = [wl.column for wl in self.wip_limits]
        if len(columns) != len(set(columns)):
            dupes = sorted(
                c.value for c, count in Counter(columns).items() if count > 1
            )
            msg = f"Duplicate WIP limit columns: {dupes}"
            logger.warning(
                KANBAN_CONFIG_VALIDATION_FAILED,
                reason="duplicate_wip_limit_columns",
                columns=dupes,
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_no_done_limit(self) -> Self:
        """Reject WIP limits on the DONE column.

        Returns:
            ``self`` unchanged when no DONE-column WIP limit exists.

        Raises:
            ValueError: When any WIP limit targets
                :attr:`KanbanColumn.DONE`.
        """
        for wl in self.wip_limits:
            if wl.column is KanbanColumn.DONE:
                msg = (
                    "WIP limits on the DONE column are not allowed "
                    "-- completed work has no capacity constraint"
                )
                logger.warning(
                    KANBAN_CONFIG_VALIDATION_FAILED,
                    reason="done_column_wip_limit",
                    column=wl.column.value,
                    limit=wl.limit,
                )
                raise ValueError(msg)
        return self


def _find_column_limit(
    config: KanbanConfig,
    column: KanbanColumn,
) -> int | None:
    """Return the WIP limit for *column*, or ``None`` if unlimited."""
    for wl in config.wip_limits:
        if wl.column == column:
            return wl.limit
    return None


def check_wip_limit(
    config: KanbanConfig,
    target_column: KanbanColumn,
    current_counts: Mapping[KanbanColumn, int],
) -> WipCheckResult:
    """Check whether moving a task into *target_column* respects WIP limits.

    The *current_counts* mapping should reflect the board state
    **before** the move (i.e. not yet including the task being moved).

    Args:
        config: Kanban board configuration.
        target_column: Column the task would move into.
        current_counts: Current task count per column.

    Returns:
        A :class:`WipCheckResult` indicating whether the move is
        allowed and the relevant counts.
    """
    current = current_counts.get(target_column, 0)
    limit = _find_column_limit(config, target_column)

    if limit is None:
        return WipCheckResult(
            allowed=True,
            column=target_column,
            current_count=current,
            limit=None,
        )

    new_count = current + 1

    if new_count == limit:
        logger.info(
            KANBAN_WIP_LIMIT_REACHED,
            column=target_column.value,
            count=new_count,
            limit=limit,
        )

    if new_count > limit:
        logger.warning(
            KANBAN_WIP_LIMIT_EXCEEDED,
            column=target_column.value,
            count=new_count,
            limit=limit,
            enforced=config.enforce_wip,
        )
        if config.enforce_wip:
            return WipCheckResult(
                allowed=False,
                column=target_column,
                current_count=current,
                limit=limit,
            )

    return WipCheckResult(
        allowed=True,
        column=target_column,
        current_count=current,
        limit=limit,
    )
