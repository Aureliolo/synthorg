# module-kind: code
"""The agents that can actually take work right now.

The work spine asks for a roster, not for the register of everyone hired.
An agent whose bound ``(provider, model)`` pair cannot serve is out, exactly
as an inactive one is, so it is absent from the pool rather than filtered
out later by every consumer that remembers to.

Expressing it here rather than inside assignment is deliberate. Availability
is a property of the agent on the day, not of one algorithm's candidate
scan, and putting it in the pool means the solo path, the team path and any
future consumer all inherit it without each re-deriving it.
"""

import asyncio
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.hr.registry_protocol import AgentRegistryProtocol
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.hr import (
    HR_AGENT_AVAILABLE_MODEL_RECOVERED,
    HR_AGENT_HEALTH_FAILED,
    HR_AGENT_UNAVAILABLE_MODEL_UNSERVICEABLE,
)
from synthorg.providers.agent_availability import (
    AgentAvailabilityReader,
    AgentUnavailability,
)

logger = get_logger(__name__)


@runtime_checkable
class AvailableRoster(Protocol):
    """The agents the work spine may staff from."""

    async def list_available(self) -> tuple[AgentIdentity, ...]:
        """Return the agents that can take work now."""
        ...

    async def get(self, agent_id: NotBlankStr) -> AgentIdentity | None:
        """Look up one agent regardless of availability."""
        ...


class ServiceabilityFilteredRoster:
    """An active roster minus the agents whose bound pair cannot serve.

    :meth:`get` deliberately does NOT filter: a project's recorded lead has
    to resolve whether or not their model is currently down, or a provider
    outage would read as an orphaned project.

    The transition into and out of unavailability is logged rather than
    stored. Availability is derived, so there is no state to keep; what an
    operator needs is the moment it changed, and remembering the previous
    answer for the length of this process is enough to report that.

    Args:
        registry: The register of hired agents.
        availability: Reads whether a bound pair can serve. ``None`` leaves
            every active agent available, which is the honest answer when
            nothing is measuring serviceability.
    """

    __slots__ = ("_availability", "_registry", "_transition_lock", "_unavailable")

    def __init__(
        self,
        registry: AgentRegistryProtocol,
        *,
        availability: AgentAvailabilityReader | None = None,
    ) -> None:
        self._registry = registry
        self._availability = availability
        self._unavailable: dict[str, AgentUnavailability] = {}
        # ``list_available`` reads ``_unavailable``, awaits once per agent,
        # then writes it back. Two concurrent callers otherwise interleave
        # read and write, and each compares its transitions against a map
        # the other has already replaced: an agent going down is announced
        # twice, or recovers with no event at all.
        self._transition_lock = asyncio.Lock()

    async def get(self, agent_id: NotBlankStr) -> AgentIdentity | None:
        """Look up one agent regardless of availability.

        Returns:
            The identity, or ``None`` when nothing is registered under
            *agent_id*.
        """
        return await self._registry.get(agent_id)

    async def list_available(self) -> tuple[AgentIdentity, ...]:
        """Return the active agents whose bound pair can serve.

        Returns:
            The staffable agents. With no availability reader wired, the
            active roster unchanged.
        """
        if self._availability is None:
            return await self._registry.list_active()
        async with self._transition_lock:
            # BOTH reads stay inside the lock, because both feed the same
            # write. Hoisting either out shortens the hold and lets two
            # callers snapshot in one order and commit in the other, so the
            # older answer lands last. Whichever read is stale, the damage
            # is a transition the log announces and nothing performed: from
            # the availability read, an agent that recovered reported still
            # out; from the roster read, an agent offboarded in between
            # reported back at work, because `_report_transitions` decides
            # recovery by membership of exactly this tuple.
            #
            # One fleet-wide availability read, then a dict lookup per
            # agent: agents share pairs, so asking per agent resolved the
            # boundaries and snapshotted the record store once per row.
            active = await self._registry.list_active()
            out = await self._unavailable_pairs(active)
            if out is None:
                # The read failed, which is not a measurement. Treating it
                # as "nobody is out" would announce recovery for everyone
                # currently out and clear the map that remembers them, so a
                # health-surface blip would read as the whole roster coming
                # back. Staffing still fails open; only the verdict is
                # withheld.
                return active
            staffable: list[AgentIdentity] = []
            still_out: dict[str, AgentUnavailability] = {}
            for agent in active:
                reason = out.get((agent.model.provider, agent.model.model_id))
                if reason is None:
                    staffable.append(agent)
                    continue
                still_out[str(agent.id)] = reason
            self._report_transitions(active, still_out)
            self._unavailable = still_out
        return tuple(staffable)

    async def _unavailable_pairs(
        self,
        active: tuple[AgentIdentity, ...],
    ) -> Mapping[tuple[str, str], AgentUnavailability] | None:
        """Read every unserviceable pair, or report that the read failed.

        Args:
            active: The roster being judged, whose bindings the reader also
                checks against the provider catalogue. A pair absent from
                the catalogue has never been called, so nothing measures it
                and only asking about it directly finds it.

        Returns:
            The pairs that cannot serve, or ``None`` when the read failed.
            ``None`` rather than an empty mapping: an empty mapping is a
            real answer meaning nobody is out, and a failed read has no
            answer to give. Collapsing the two lets a fault masquerade as a
            fleet-wide recovery.
        """
        assert self._availability is not None  # noqa: S101  # caller checks
        try:
            return await self._availability.unavailability_by_pair(
                [(agent.model.provider, agent.model.model_id) for agent in active]
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- availability narrows the roster, so a
            # failed read must leave every agent staffable; the alternative is
            # a health-surface fault emptying the company
            reraise_critical(exc)
            logger.warning(
                HR_AGENT_HEALTH_FAILED,
                operation="availability_read",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None

    def _report_transitions(
        self,
        active: tuple[AgentIdentity, ...],
        still_out: dict[str, AgentUnavailability],
    ) -> None:
        """Log agents entering and leaving unavailability.

        Only agents on the current active roster are considered recovered,
        so an agent that was offboarded while its model was down is not
        announced as back at work.
        """
        for agent_id, reason in still_out.items():
            if agent_id in self._unavailable:
                continue
            logger.warning(
                HR_AGENT_UNAVAILABLE_MODEL_UNSERVICEABLE,
                agent_id=agent_id,
                provider=str(reason.provider_name),
                model=str(reason.model),
                outcome_class=(
                    reason.outcome_class.value
                    if reason.outcome_class is not None
                    else None
                ),
                since=reason.since.isoformat() if reason.since is not None else None,
                needs_operator=reason.needs_operator,
            )
        active_ids = {str(agent.id) for agent in active}
        for agent_id, reason in self._unavailable.items():
            if agent_id in still_out or agent_id not in active_ids:
                continue
            logger.info(
                HR_AGENT_AVAILABLE_MODEL_RECOVERED,
                agent_id=agent_id,
                provider=str(reason.provider_name),
                model=str(reason.model),
            )


__all__ = ["AvailableRoster", "ServiceabilityFilteredRoster"]
