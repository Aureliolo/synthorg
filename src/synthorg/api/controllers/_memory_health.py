# module-kind: code
"""Agent-memory health for the readiness surface.

Split from ``health.py`` so the memory verdict, which reads the wiring
and probes the live backend across several degraded states, does not
push the controller over its size budget.

Memory failing silently is the defect this exists to prevent: keyword-
only recall answers every query, so a lost dense index reads as working
memory unless the surface probes for it.
"""

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.state import AppState


class ProbeService(Protocol):
    """The probe half of ``health._probe_service`` this module needs.

    Passed in rather than imported so ``health`` stays the only importer
    and no import cycle forms.
    """

    async def __call__(
        self,
        *,
        configured: bool,
        probe: Callable[[], Awaitable[bool]],
        component: str,
    ) -> bool | None:
        """Probe a configured async service, or return ``None``."""
        ...


class MemoryState(StrEnum):
    """How agent memory is running, for the operator-facing banner.

    Attributes:
        DURABLE: Wired on a store that survives restart and retrieves
            by meaning.
        DEGRADED: Wired and answering correctly, but not fully: the
            ephemeral keyword store, the built-in lexical embedder, a
            missing dense index, or maintenance off. Costs latency or
            recall quality, never correctness.
        UNREACHABLE: Wired but not answering. Reads and writes are
            failing, so this is the one memory state that gates traffic.
        OFF: Not wired. Usually no embedding model chosen, which the
            startup log records at ERROR.
    """

    DURABLE = "durable"
    DEGRADED = "degraded"
    UNREACHABLE = "unreachable"
    OFF = "off"

    @property
    def readiness(self) -> bool | None:
        """This state's contribution to the readiness verdict.

        Carried here rather than in a branch table beside the caller so a
        state added later cannot be given a meaning in one place and
        forgotten in the other.

        Returns:
            ``False`` to block traffic, ``True`` to count as ready, and
            ``None`` to abstain: a degradation still returns correct
            results, so failing readiness for one would take a working
            system offline and collapse "recall got slower" into "recall
            stopped".
        """
        if self is MemoryState.UNREACHABLE:
            return False
        if self is MemoryState.DURABLE:
            return True
        return None


class MemoryHealth(BaseModel):
    """Agent-memory substrate state.

    Memory failing silently is the defect this whole surface exists to
    prevent: an operator whose memory never wired saw a healthy system
    that simply never remembered anything.

    Attributes:
        state: How memory is running.
        backend: Configured backend name.
        detail: What an operator should do about it, when anything.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    state: MemoryState = Field(description="How agent memory is running")
    backend: str = Field(description="Configured memory backend name")
    detail: str | None = Field(
        default=None,
        description="Operator-facing remedy, when action is needed",
    )


def memory_wiring_health(app_state: AppState) -> MemoryHealth | None:
    """Judge agent memory from its wiring alone, without a probe.

    Returns:
        The verdict when the wiring already settles it, or ``None`` when
        a live probe is needed to tell healthy from degraded.
    """
    from synthorg.memory.factory import IN_MEMORY_BACKEND  # noqa: PLC0415
    from synthorg.memory.state import MemoryStateSlice  # noqa: PLC0415

    backend_name = app_state.config.memory.backend
    if app_state.slice(MemoryStateSlice).backend is None:
        return MemoryHealth(
            state=MemoryState.OFF,
            backend=backend_name,
            detail=(
                "No memory backend is wired, so agents start every task "
                "with no recall. The usual cause is that no embedding "
                "model resolved: set memory.embedder_model to a "
                "provider-bound reference, or connect a provider that "
                "offers an embedding model."
            ),
        )
    if backend_name == IN_MEMORY_BACKEND:
        return MemoryHealth(
            state=MemoryState.DEGRADED,
            backend=backend_name,
            detail=(
                "The ephemeral backend matches by term and loses every "
                "memory on restart. Switch memory.backend to 'sqlvector' "
                "for durable, meaning-based recall."
            ),
        )
    return None


def _runtime_degradation(
    backend_name: str,
    *,
    builtin_embedder: bool,
    dense_available: bool,
    dense_indexed: bool,
    consolidation_running: bool,
) -> MemoryHealth | None:
    """Name the fault a live, probe-passing backend is still carrying.

    Taken as plain flags rather than the backend itself so this module
    keeps importing no memory types, which is what stops an import cycle
    forming back through ``health``.

    Returns:
        The degradation to report, or ``None`` when memory is durable.
    """
    if builtin_embedder:
        return MemoryHealth(
            state=MemoryState.DEGRADED,
            backend=backend_name,
            detail=(
                "Recall is running on the built-in embedder, which matches "
                "shared vocabulary rather than meaning, so agents get "
                "literal term overlap instead of related memories. Choose "
                "an embedding model in settings to recall by meaning."
            ),
        )
    if not dense_available:
        return MemoryHealth(
            state=MemoryState.DEGRADED,
            backend=backend_name,
            detail=(
                "Recall is keyword-only: the dense vector index is not "
                "available, so agents get literal term matches instead of "
                "related memories. See the memory.dense_index.* log "
                "events for the cause."
            ),
        )
    if not dense_indexed:
        return MemoryHealth(
            state=MemoryState.DEGRADED,
            backend=backend_name,
            detail=(
                "Dense recall works but is unindexed: every search reads "
                "the whole corpus, so latency grows with it. See the "
                "memory.dense_index.* log events for which condition "
                "applies."
            ),
        )
    if not consolidation_running:
        return MemoryHealth(
            state=MemoryState.DEGRADED,
            backend=backend_name,
            detail=(
                "Recall works but maintenance is not running: retention "
                "and per-agent memory caps are not being enforced, so "
                "memory grows without bound. Check memory.consolidation_"
                "interval and the consolidation.scheduler.* log events."
            ),
        )
    return None


async def resolve_memory_health(
    app_state: AppState,
    *,
    probe: ProbeService,
) -> MemoryHealth:
    """Report whether agent memory is actually running.

    Reads the wiring *and* probes the live backend. A wired backend can
    still have lost its store or its dense index after boot, and those
    are the degradations an operator is least likely to anticipate:
    keyword-only recall answers every query, so it reads as working
    memory rather than as a fault.

    Args:
        app_state: The live application state.
        probe: The controller's service-probe helper, applied to the
            backend's health check.

    Returns:
        ``MemoryHealth`` describing the substrate and, when it is not
        durable, what the operator should do.
    """
    from synthorg.memory.state import MemoryStateSlice  # noqa: PLC0415

    backend_name = app_state.config.memory.backend
    memory_slice = app_state.slice(MemoryStateSlice)
    backend = memory_slice.backend
    settled = memory_wiring_health(app_state)
    if settled is not None or backend is None:
        return settled or MemoryHealth(state=MemoryState.OFF, backend=backend_name)
    healthy = await probe(
        configured=True,
        probe=backend.health_check,
        component="memory",
    )
    if not healthy:
        return MemoryHealth(
            state=MemoryState.UNREACHABLE,
            backend=backend_name,
            detail=(
                "The memory backend is wired but did not answer a health "
                "probe, so reads and writes are failing. Check the "
                "database the memory tables live in."
            ),
        )
    from synthorg.memory.embedding.hashing import BUILTIN_EMBEDDER_REF  # noqa: PLC0415

    degraded = _runtime_degradation(
        backend_name,
        builtin_embedder=memory_slice.embedder_ref == BUILTIN_EMBEDDER_REF,
        dense_available=backend.supports_dense_search,
        dense_indexed=backend.dense_search_indexed,
        consolidation_running=memory_slice.consolidation_scheduler is not None,
    )
    return degraded or MemoryHealth(state=MemoryState.DURABLE, backend=backend_name)


__all__ = [
    "MemoryHealth",
    "MemoryState",
    "memory_wiring_health",
    "resolve_memory_health",
]
