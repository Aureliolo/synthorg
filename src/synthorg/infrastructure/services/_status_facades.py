# module-kind: service
# ruff: noqa: EM101
"""Status / read facades for the setup, simulation, audit, events, and health tools.

These facades surface a read-only window (or a capability-gap error)
over the corresponding AppState primitive for the MCP handler layer.

Primitives are stored internally as :class:`Any` so the facade can
introspect capabilities at runtime (``getattr`` + ``callable`` checks)
without fighting protocol-type narrowing when the primitive is still
evolving.

The file-level ``EM101`` / ``E501`` suppressions are intentional:
capability-gap messages are string literals passed straight to
:class:`CapabilityNotSupportedError`, and the long-form capability
descriptions read better on one line for grep-ability than broken
across multiple.
"""

from typing import TYPE_CHECKING, Any, cast

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.infrastructure.services._shared import _DEFAULT_LIMIT, _require_callable

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from synthorg.client.simulation_state import ClientSimulationState
    from synthorg.communication.event_stream.stream import EventStreamHub
    from synthorg.core.types import NotBlankStr
    from synthorg.integrations.health.prober import HealthProberService
    from synthorg.security.audit import AuditLog


class SetupFacadeService:
    """Setup status + initialisation facade."""

    def __init__(self) -> None:
        self._initialised_at: datetime | None = None

    async def get_status(self) -> Mapping[str, object]:
        """Return the initialisation status + timestamp (when initialised)."""
        return {
            "initialised": self._initialised_at is not None,
            "initialised_at": (
                self._initialised_at.isoformat()
                if self._initialised_at is not None
                else None
            ),
        }

    async def initialize(
        self,
        *,
        config: Mapping[str, object],  # noqa: ARG002 - part of public contract
    ) -> None:
        """Capability gap -- setup runs through the controller + CLI wizard.

        Raises:
            CapabilityNotSupportedError: Always; initialisation is driven
                through the setup controller and CLI wizard, not over MCP.
        """
        raise CapabilityNotSupportedError(
            "setup_initialize",
            "initialisation is driven through the setup controller + CLI wizard",
        )


class SimulationFacadeService:
    """Facade over :class:`ClientSimulationState`."""

    def __init__(self, *, state: ClientSimulationState) -> None:
        self._state = cast("Any", state)

    async def list_simulations(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[tuple[object, ...], int]:
        """Return paginated simulation scenarios plus the unfiltered total.

        Returns:
            A ``(page, total)`` pair: the scenarios for the requested
            slice and the unfiltered count.

        Raises:
            ValueError: If ``offset`` is negative, or ``limit`` is
                provided and non-positive.
            CapabilityNotSupportedError: If the state object does not
                expose ``list_scenarios``.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            raise ValueError(msg)
        if limit is not None and limit < 1:
            msg = f"limit must be >= 1 when provided, got {limit}"
            raise ValueError(msg)
        fn = _require_callable(
            self._state,
            "list_scenarios",
            "simulation_list",
            "ClientSimulationState does not expose list_scenarios",
        )
        all_scenarios = tuple(fn())
        total = len(all_scenarios)
        end = total if limit is None else offset + limit
        return all_scenarios[offset:end], total

    async def get_simulation(self, simulation_id: NotBlankStr) -> object | None:
        """Fetch a scenario by id or ``None`` if absent.

        Returns:
            The scenario for ``simulation_id``, or ``None`` when absent.
        """
        fn = _require_callable(
            self._state,
            "get_scenario",
            "simulation_get",
            "ClientSimulationState does not expose get_scenario",
        )
        return cast("object | None", fn(simulation_id))

    async def create_simulation(self) -> None:
        """Capability gap -- scenarios are loaded from config at start-up.

        Raises:
            CapabilityNotSupportedError: Always; simulation scenarios are
                loaded from config at start-up, not created over MCP.
        """
        raise CapabilityNotSupportedError(
            "simulation_create",
            "simulation scenarios are loaded from config at start-up",
        )


class AuditReadService:
    """Read facade over :class:`AuditLog`."""

    def __init__(self, *, audit_log: AuditLog) -> None:
        self._audit = cast("Any", audit_log)

    async def list_entries(
        self,
        *,
        offset: int = 0,
        limit: int = _DEFAULT_LIMIT,
    ) -> tuple[tuple[object, ...], int]:
        """Return paginated audit entries plus the unfiltered total.

        Returns:
            A ``(page, total)`` pair: the audit entries for the requested
            slice and the unfiltered count.

        Raises:
            ValueError: If ``offset`` is negative or ``limit`` < 1.
            CapabilityNotSupportedError: If the underlying
                :class:`AuditLog` does not expose ``list_entries``.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            raise ValueError(msg)
        if limit < 1:
            msg = f"limit must be >= 1, got {limit}"
            raise ValueError(msg)
        fn = getattr(self._audit, "list_entries", None)
        if not callable(fn):
            raise CapabilityNotSupportedError(
                "audit_list",
                "AuditLog does not expose list_entries",
            )
        all_entries = tuple(fn())
        total = len(all_entries)
        page = all_entries[offset : offset + limit]
        return page, total


class EventsReadService:
    """Read facade over :class:`EventStreamHub`."""

    def __init__(self, *, hub: EventStreamHub) -> None:
        self._hub = cast("Any", hub)

    async def list_events(
        self,
        *,
        offset: int = 0,
        limit: int = _DEFAULT_LIMIT,
    ) -> tuple[tuple[object, ...], int]:
        """Return paginated recent events plus the unfiltered total.

        Fetches the hub's full retained buffer so ``total`` reflects
        the true count (not just ``offset + limit``), then slices the
        requested window.

        Returns:
            A ``(page, total)`` pair: the events for the requested slice
            and the hub's full retention count.

        Raises:
            ValueError: If ``offset`` is negative or ``limit`` < 1.
            CapabilityNotSupportedError: If the underlying
                :class:`EventStreamHub` does not expose ``recent_events``.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            raise ValueError(msg)
        if limit < 1:
            msg = f"limit must be >= 1, got {limit}"
            raise ValueError(msg)
        fn = getattr(self._hub, "recent_events", None)
        if not callable(fn):
            raise CapabilityNotSupportedError(
                "events_list",
                "EventStreamHub does not expose recent_events",
            )
        # Ask for every retained event so ``total`` is the hub's full
        # retention count rather than the pagination window size.  The
        # hub keeps a bounded ring buffer so this is not unbounded.
        all_events = tuple(fn())
        total = len(all_events)
        page = all_events[offset : offset + limit]
        return page, total


class IntegrationHealthFacadeService:
    """Read facade over :class:`HealthProberService`."""

    def __init__(self, *, prober: HealthProberService) -> None:
        self._prober = cast("Any", prober)

    async def get_all(self) -> Mapping[str, object]:
        """Return the full integration-health snapshot.

        Raises:
            CapabilityNotSupportedError: When the prober does not expose
                ``snapshot``.
        """
        fn = getattr(self._prober, "snapshot", None)
        if not callable(fn):
            raise CapabilityNotSupportedError(
                "integration_health_list",
                "HealthProberService does not expose snapshot",
            )
        return dict(fn())

    async def get_one(self, integration_id: NotBlankStr) -> object | None:
        """Return the health entry for ``integration_id`` or ``None``."""
        snapshot = await self.get_all()
        return snapshot.get(integration_id)
