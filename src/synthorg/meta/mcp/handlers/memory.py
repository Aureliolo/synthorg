"""Memory domain MCP handlers (fine-tune checkpoints + runs + entries).

Wires 12 tools through :class:`MemoryService`. The handler bodies live
in sibling modules: fine-tune lifecycle + runs + embedder in
``memory_finetune``, checkpoints in ``memory_checkpoints``, and
memory-entry deletion in ``memory_entries``; the service-resolution
helpers live in ``_memory_service_helpers``. This module aggregates them
into the read-only ``MEMORY_HANDLERS`` map.

Handlers route through the injected ``MemoryService`` facade exclusively
and never reach into ``app_state.persistence.*`` directly (CLAUDE.md
persistence-boundary rule). ``MemoryBackendUnsupportedError`` is
forwarded to the shared ``not_supported`` envelope.

Privileged ops. ``start_fine_tune`` / ``resume_fine_tune`` (which launch
the pipeline, including the internal model-swapping deploy stage),
``deploy_checkpoint`` (a standalone model swap), and the destructive
``cancel_fine_tune`` / ``rollback_checkpoint`` / ``delete_checkpoint`` /
``delete_entry`` all enforce the guardrail triple (actor + ``confirm`` +
``reason``) at the handler boundary and emit ``MCP_ADMIN_OP_EXECUTED`` on
success.
"""

from collections.abc import Mapping
from types import MappingProxyType

from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers.memory_checkpoints import (
    _memory_delete_checkpoint,
    _memory_deploy_checkpoint,
    _memory_list_checkpoints,
    _memory_rollback_checkpoint,
)
from synthorg.meta.mcp.handlers.memory_entries import _memory_delete_entry
from synthorg.meta.mcp.handlers.memory_finetune import (
    _memory_cancel_fine_tune,
    _memory_get_active_embedder,
    _memory_get_fine_tune_status,
    _memory_list_runs,
    _memory_resume_fine_tune,
    _memory_run_preflight,
    _memory_start_fine_tune,
)

MEMORY_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_memory_start_fine_tune": _memory_start_fine_tune,
        "synthorg_memory_resume_fine_tune": _memory_resume_fine_tune,
        "synthorg_memory_get_fine_tune_status": _memory_get_fine_tune_status,
        "synthorg_memory_cancel_fine_tune": _memory_cancel_fine_tune,
        "synthorg_memory_run_preflight": _memory_run_preflight,
        "synthorg_memory_list_checkpoints": _memory_list_checkpoints,
        "synthorg_memory_deploy_checkpoint": _memory_deploy_checkpoint,
        "synthorg_memory_rollback_checkpoint": _memory_rollback_checkpoint,
        "synthorg_memory_delete_checkpoint": _memory_delete_checkpoint,
        "synthorg_memory_list_runs": _memory_list_runs,
        "synthorg_memory_get_active_embedder": _memory_get_active_embedder,
        "synthorg_memory_delete_entry": _memory_delete_entry,
    }
)
