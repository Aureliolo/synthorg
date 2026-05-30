# module-kind: controller
"""Memory entry deletion endpoint (CEO / SYSTEM only)."""

from litestar import Controller, delete
from litestar.datastructures import State

from synthorg.api.controllers.memory import _shared
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_roles
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.core.auth.roles import HumanRole
from synthorg.core.domain_errors import (
    FeatureNotImplementedError,
    MemoryEntryNotFoundError,
)
from synthorg.memory.fine_tune_plan import MemoryBackendUnsupportedError
from synthorg.observability import safe_error_description


class MemoryEntriesController(Controller):
    """Per-agent memory entry deletion."""

    path = "/admin/memory"
    tags = ("admin", "memory")
    guards = [require_roles(HumanRole.CEO, HumanRole.SYSTEM)]  # noqa: RUF012

    @delete(
        "/agents/{agent_id:str}/memories/{memory_id:str}",
        status_code=200,
        guards=[
            per_op_rate_limit_from_policy(
                "memory.entry_delete",
                key="user",
            ),
        ],
    )
    async def delete_memory_entry(
        self,
        state: State,
        agent_id: PathId,
        memory_id: PathId,
    ) -> ApiResponse[None]:
        """Delete a single memory entry owned by an agent.

        Args:
            state: Application state.
            agent_id: Owning agent identifier (1-128 chars, enforced
                at the path-parameter boundary by ``PathId``).
            memory_id: Memory entry identifier (1-128 chars, enforced
                at the path-parameter boundary by ``PathId``).

        Returns ``200 OK`` on success and ``404 Not Found`` when the
        memory entry does not exist (or the agent has no entry with
        that id). Returns ``501 Not Implemented`` when no memory
        backend is wired on the active app state.

        Returns:
            ``ApiResponse[None]`` instance.

        Raises:
            MemoryEntryNotFoundError: Raised on the corresponding failure path.
            FeatureNotImplementedError: Raised on the corresponding failure path.
        """
        # ``require_fine_tune=False`` -- entry deletion only needs the
        # ``MemoryBackend``; eagerly resolving the fine-tune repos
        # would 501 every memory-only deployment, which the
        # ``DELETE /memory/entries/...`` path must support.
        service = _shared.build_memory_service(state.app_state, require_fine_tune=False)
        try:
            deleted = await service.delete_memory_entry(
                agent_id,
                memory_id,
            )
        except MemoryBackendUnsupportedError as exc:
            # ``MemoryService.delete_memory_entry`` already emits
            # ``MEMORY_ENTRY_DELETE_FAILED`` for this branch, so the
            # controller stays in the layering role of HTTP
            # translation only and does not double-record the event.
            raise FeatureNotImplementedError(
                safe_error_description(exc),
            ) from exc
        if not deleted:
            # ``MemoryService.delete_memory_entry`` emits
            # ``MEMORY_ENTRY_DELETE_FAILED`` with ``reason="not_found"``
            # for this branch, so the controller stays in the layering
            # role of HTTP translation only.
            msg = f"memory entry {memory_id!r} not found"
            raise MemoryEntryNotFoundError(msg)
        return ApiResponse(data=None)
