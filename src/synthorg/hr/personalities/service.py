"""Personality preset MCP-facing service.

Thin facade over :class:`PersonalityPresetService` that
- returns ``(page, total)`` from ``list_personalities`` for the MCP
  pagination envelope, and
- returns ``None`` from ``get_personality`` for missing names so
  handlers map onto ``not_found`` envelopes (the underlying service
  still raises :class:`NotFoundError` for REST controllers).

The underlying service is the authoritative CRUD surface; this
module is intentionally narrow.
"""

from typing import TYPE_CHECKING

from synthorg.core.domain_errors import NotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.preset import (
    PRESET_INVALID_REQUEST,
    PRESET_NOT_FOUND,
)

if TYPE_CHECKING:
    from synthorg.templates.preset_service import (
        PersonalityPresetService,
        PresetEntry,
    )

logger = get_logger(__name__)


class PersonalityService:
    """MCP-facing facade over :class:`PersonalityPresetService`.

    Constructor:
        presets: The underlying preset service (builtin + custom).
    """

    __slots__ = ("_presets",)

    def __init__(
        self,
        *,
        presets: PersonalityPresetService,
    ) -> None:
        """Initialise with the preset-service dependency."""
        self._presets = presets

    async def list_personalities(
        self,
        *,
        offset: int,
        limit: int,
    ) -> tuple[tuple[PresetEntry, ...], int]:
        """Return a page of personalities + the total count.

        The preset service returns the full (small, builtin + custom)
        set in one call. Pagination is applied in-process so the MCP
        envelope carries the same ``(page, total)`` shape as every
        other list tool.

        Args:
            offset: Page offset (>= 0).
            limit: Page size (> 0).

        Returns:
            Tuple of ``(page, total)``.

        Raises:
            ValueError: If ``offset`` is negative or ``limit`` is not
                strictly positive.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            logger.warning(
                PRESET_INVALID_REQUEST,
                param="offset",
                value=offset,
            )
            raise ValueError(msg)
        if limit < 1:
            msg = f"limit must be >= 1, got {limit}"
            logger.warning(
                PRESET_INVALID_REQUEST,
                param="limit",
                value=limit,
            )
            raise ValueError(msg)
        all_entries = await self._presets.list_all()
        total = len(all_entries)
        page = all_entries[offset : offset + limit]
        return page, total

    async def get_personality(
        self,
        name: NotBlankStr,
    ) -> PresetEntry | None:
        """Fetch a personality by name or ``None`` if missing.

        Args:
            name: Preset identifier (any case; normalised by the
                underlying service).

        Returns:
            The :class:`PresetEntry`, or ``None`` if no preset with
            this name exists.
        """
        try:
            return await self._presets.get(str(name))
        except NotFoundError:
            logger.debug(PRESET_NOT_FOUND, preset_name=str(name))
            return None


__all__ = ["PersonalityService"]
