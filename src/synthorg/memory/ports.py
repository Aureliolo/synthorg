"""Structural ports for ``MemoryService``'s injected collaborators.

``MemoryService`` and ``FineTuneAdminService`` depend on a runtime
settings accessor and a fine-tune orchestrator.  Rather than couple to
the concrete ``SettingsService`` (which lives in the ``settings``
package) and ``FineTuneOrchestrator`` classes, they depend on these
narrow ``@runtime_checkable`` protocols describing only the surface the
memory layer actually uses.  This keeps the dependency direction
pointing at an abstraction the memory package owns and lets test doubles
conform structurally.
"""

from typing import Protocol, runtime_checkable

from synthorg.memory.embedding.fine_tune_models import (
    FineTuneRequest,
    FineTuneRun,
    FineTuneStatus,
)
from synthorg.settings.models import SettingValue


@runtime_checkable
class SettingsAccessor(Protocol):
    """Read/write surface of the settings service used by memory flows.

    The concrete ``SettingsService`` satisfies this protocol; its
    richer ``set`` signature (CAS, import-source) is irrelevant here
    because the memory layer only writes plain values and never reads
    the returned entry.
    """

    async def get(self, namespace: str, key: str) -> SettingValue:
        """Resolve a setting value, raising if the key is unknown."""
        ...

    async def set(self, namespace: str, key: str, value: str) -> object:
        """Persist a setting value (returned entry is intentionally opaque)."""
        ...

    async def delete(self, namespace: str, key: str) -> None:
        """Delete a setting override."""
        ...


@runtime_checkable
class FineTuneOrchestratorPort(Protocol):
    """Lifecycle surface of the fine-tune orchestrator used by memory.

    The concrete ``FineTuneOrchestrator`` satisfies this protocol.  Only
    the run-lifecycle operations the admin service drives are exposed;
    startup-recovery and WS-plumbing internals stay off the contract.
    """

    @property
    def current_run(self) -> FineTuneRun | None:
        """The in-memory active run, or ``None`` when idle."""
        ...

    async def start(self, request: FineTuneRequest) -> FineTuneRun:
        """Start a new pipeline run."""
        ...

    async def resume(self, run_id: str) -> FineTuneRun:
        """Resume a failed run from its last completed stage."""
        ...

    async def cancel(self) -> None:
        """Cancel the active run, if any."""
        ...

    async def get_status(self) -> FineTuneStatus:
        """Return the orchestrator's view of run status."""
        ...
