"""A ``tzinfo`` that carries no offset, for testing awareness guards.

Aware means two things, not one: a datetime is naive when ``tzinfo`` is
``None`` OR when ``tzinfo.utcoffset(d)`` returns ``None``. The second half
is the one a ``tzinfo is None`` check silently passes, and it is not
theoretical: ``astimezone`` reads such a value as LOCAL wall-clock time and
converts it, so a guard that only tests the first half lets exactly the
instant-shift it was written to stop through the branch it thought it had
already covered.

This is the smallest legal object with that property, so a guard can be
tested against both halves rather than against the easy one.
"""

from datetime import datetime, timedelta, tzinfo
from typing import override


class OffsetlessTz(tzinfo):
    """A ``tzinfo`` that declines to name an offset."""

    @override
    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        """Return no offset.

        Returns:
            ``None``, always.
        """
        return None

    @override
    def dst(self, dt: datetime | None) -> timedelta | None:
        """Return no daylight-saving adjustment.

        Returns:
            ``None``, always.
        """
        return None

    @override
    def tzname(self, dt: datetime | None) -> str | None:
        """Return no zone name.

        Returns:
            ``None``, always.
        """
        return None


#: Shared instance. ``tzinfo`` subclasses are immutable and stateless here,
#: so one is enough.
OFFSETLESS_TZ = OffsetlessTz()


__all__ = ["OFFSETLESS_TZ", "OffsetlessTz"]
