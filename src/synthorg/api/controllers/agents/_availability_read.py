# module-kind: code
"""Reading one agent's availability for a controller that only annotates it.

Kept beside :mod:`observability` rather than inside it because the two
surfaces that carry this (the agent health snapshot and the runtime roster)
share one rule: the verdict decorates the response, so a health-surface
fault must not take the response with it. A read that 500s because the
tracker is unwell is strictly worse than one reporting nobody out.

That is deliberately NOT what ``ServiceabilityFilteredRoster`` does with a
failed read, and the difference is in what the two are answering with. The
engine's answer becomes remembered state and an emitted transition, so
reading a failure as "nobody is out" there announces a recovery that never
happened and destroys the record that would have corrected it. Here the
answer is one field of one response, recomputed on the next request and
remembered by nothing, so the optimistic reading costs a stale field until
the tracker is well again.
"""

from collections.abc import Mapping

from synthorg.api.state import AppState
from synthorg.core.agent import ModelConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.hr import HR_AGENT_HEALTH_FAILED
from synthorg.providers.agent_availability import (
    AgentUnavailability,
    ServiceabilityAvailabilityReader,
)
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)


async def unavailable_pairs(
    app_state: AppState,
) -> Mapping[tuple[str, str], AgentUnavailability]:
    """Read every unserviceable pair, treating a read failure as available.

    One fleet-wide read joined by pair rather than a lookup per row: agents
    share models, and a roster page should not cost a snapshot per agent to
    answer one question about each.

    Routed through the same reader as :func:`unavailability_or_none` rather
    than calling the tracker directly, because the reader is what resolves
    the operator's verdict boundaries live. Snapshotting the tracker without
    them would let the roster and the per-agent read disagree about the same
    pair, using thresholds nobody set against thresholds somebody did.

    Returns:
        The pairs that cannot serve; empty when nothing measures them or the
        read failed.
    """
    tracker = app_state.slice(ProvidersStateSlice).health_tracker
    if tracker is None:
        return {}
    try:
        reader = ServiceabilityAvailabilityReader(
            tracker,
            config_resolver=config_resolver_of(app_state),
        )
        return await reader.unavailability_by_pair()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            HR_AGENT_HEALTH_FAILED,
            operation="availability_read",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return {}


async def unavailability_or_none(
    app_state: AppState,
    model: ModelConfig,
) -> AgentUnavailability | None:
    """Read why *model*'s pair cannot serve, or ``None``.

    Returns:
        The reason the agent is out; ``None`` when the pair serves, when no
        health tracker is wired (an installation measuring nothing has no
        grounds to call an agent unavailable), or when the read itself
        failed.
    """
    tracker = app_state.slice(ProvidersStateSlice).health_tracker
    if tracker is None:
        return None
    reader = ServiceabilityAvailabilityReader(
        tracker,
        config_resolver=config_resolver_of(app_state),
    )
    try:
        return await reader.unavailability_for(model)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            HR_AGENT_HEALTH_FAILED,
            operation="availability_read",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


__all__ = ["unavailability_or_none", "unavailable_pairs"]
