# module-kind: code
"""Shared recheck-freshness policy for connection health verdicts.

Two callers decide whether a connection is due for a probe: the background
prober loop, and the dashboard's aggregate-health endpoint. They must agree,
because a probe is not free. A connection pointed at a metered third-party
API bills per call, so re-proving a working credential is spending real quota
to learn nothing.

They did not agree. The prober gated on freshness while the endpoint probed
every connection on the page unconditionally, and the Connections view polls
that endpoint every thirty seconds, so the gate was bypassed by exactly the
path that mattered. Keeping the policy in one module means the next caller
inherits it rather than reimplementing half of it.

Intervals arrive as plain seconds rather than a config object so this stays a
leaf both the service layer and the controller can import.
"""

from datetime import datetime

from synthorg.integrations.connections.models import Connection, ConnectionStatus


def recheck_interval_seconds(
    status: ConnectionStatus,
    *,
    healthy_seconds: int,
    degraded_seconds: int,
    unhealthy_seconds: int,
) -> int:
    """How long a verdict of *status* stays trusted.

    Driven by the outcome rather than the clock alone: a connection that
    answered correctly is unlikely to have changed, while one that failed is
    what the operator is watching, so the two deserve very different
    cadences.

    Returns:
        Seconds this status is trusted before the connection is due again.
    """
    if status is ConnectionStatus.HEALTHY:
        return healthy_seconds
    if status is ConnectionStatus.UNHEALTHY:
        return unhealthy_seconds
    return degraded_seconds


def is_probe_due(
    connection: Connection,
    *,
    now: datetime,
    healthy_seconds: int,
    degraded_seconds: int,
    unhealthy_seconds: int,
) -> bool:
    """Whether *connection*'s last verdict has expired.

    Returns:
        ``True`` when it has never been checked, or when the interval for the
        status it last reported has elapsed.
    """
    last = connection.health.last_check_at
    if last is None:
        return True
    elapsed = (now - last).total_seconds()
    interval: float = recheck_interval_seconds(
        connection.health.status,
        healthy_seconds=healthy_seconds,
        degraded_seconds=degraded_seconds,
        unhealthy_seconds=unhealthy_seconds,
    )
    # An upstream that answered with a rate limit has already said how long to
    # wait. Probing sooner cannot succeed, and spends a request to be refused
    # again, so its answer is a floor on our own interval rather than advice.
    retry_after = connection.health.retry_after_seconds
    if retry_after is not None:
        interval = max(interval, retry_after)
    return elapsed >= interval


__all__ = ["is_probe_due", "recheck_interval_seconds"]
