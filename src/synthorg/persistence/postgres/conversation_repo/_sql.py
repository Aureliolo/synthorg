# module-kind: declarative
"""Bounds shared by both Postgres conversation repositories.

The page ceiling belongs to neither entity on its own: a header page and a
turn page are clamped by the same limit because the same operator surface
requests them, and two copies of the number would drift the first time one
was retuned.
"""

from typing import Final

#: Hard ceiling on a page, whatever the caller asked for.
MAX_PAGE_LIMIT: Final[int] = 1_000

__all__ = ["MAX_PAGE_LIMIT"]
