"""Billing period computation utilities.

Pure functions for determining billing period boundaries based on a
configurable reset day. Used by :class:`~synthorg.budget.enforcer.BudgetEnforcer`
to scope cost queries to the current billing cycle.
"""

from datetime import UTC, datetime


def billing_period_start(
    reset_day: int,
    *,
    now: datetime | None = None,
) -> datetime:
    """Compute the UTC-aware start of the current billing period.

    If ``now.day >= reset_day``, returns current month's ``reset_day``
    at 00:00 UTC.  Otherwise, returns previous month's ``reset_day``
    at 00:00 UTC.

    Args:
        reset_day: Day of month when the billing period resets (1-28).
        now: Reference timestamp.  Defaults to ``datetime.now(UTC)``.

    Returns:
        UTC-aware datetime at midnight on the billing period start day.

    Raises:
        ValueError: If ``reset_day`` is not in ``[1, 28]``.
    """
    if not 1 <= reset_day <= 28:  # noqa: PLR2004
        msg = f"reset_day must be 1-28, got {reset_day}"
        raise ValueError(msg)

    if now is None:
        now = datetime.now(UTC)

    if now.day >= reset_day:
        return datetime(now.year, now.month, reset_day, tzinfo=UTC)

    # Roll back to previous month
    if now.month == 1:
        return datetime(now.year - 1, 12, reset_day, tzinfo=UTC)
    return datetime(now.year, now.month - 1, reset_day, tzinfo=UTC)


def daily_period_start(*, now: datetime | None = None) -> datetime:
    """Compute the UTC-aware start of today (midnight UTC).

    Args:
        now: Reference timestamp.  Defaults to ``datetime.now(UTC)``.

    Returns:
        UTC-aware datetime at midnight of the current day.
    """
    if now is None:
        now = datetime.now(UTC)
    return datetime(now.year, now.month, now.day, tzinfo=UTC)
