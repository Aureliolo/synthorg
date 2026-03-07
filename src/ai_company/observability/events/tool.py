"""Tool event constants."""

from typing import Final

TOOL_REGISTRY_BUILT: Final[str] = "tool.registry.built"
TOOL_REGISTRY_DUPLICATE: Final[str] = "tool.registry.duplicate"
TOOL_NOT_FOUND: Final[str] = "tool.not_found"
TOOL_INVOKE_START: Final[str] = "tool.invoke.start"
TOOL_INVOKE_SUCCESS: Final[str] = "tool.invoke.success"
TOOL_INVOKE_TOOL_ERROR: Final[str] = "tool.invoke.tool_error"
TOOL_INVOKE_NOT_FOUND: Final[str] = "tool.invoke.not_found"
TOOL_INVOKE_PARAMETER_ERROR: Final[str] = "tool.invoke.parameter_error"
TOOL_INVOKE_SCHEMA_ERROR: Final[str] = "tool.invoke.schema_error"
TOOL_INVOKE_EXECUTION_ERROR: Final[str] = "tool.invoke.execution_error"
TOOL_INVOKE_DEEPCOPY_ERROR: Final[str] = "tool.invoke.deepcopy_error"
TOOL_INVOKE_NON_RECOVERABLE: Final[str] = "tool.invoke.non_recoverable"
TOOL_INVOKE_VALIDATION_UNEXPECTED: Final[str] = "tool.invoke.validation_unexpected"
TOOL_BASE_INVALID_NAME: Final[str] = "tool.base.invalid_name"
TOOL_REGISTRY_CONTAINS_TYPE_ERROR: Final[str] = "tool.registry.contains_type_error"
TOOL_INVOKE_ALL_START: Final[str] = "tool.invoke_all.start"
TOOL_INVOKE_ALL_COMPLETE: Final[str] = "tool.invoke_all.complete"
TOOL_PERMISSION_DENIED: Final[str] = "tool.permission.denied"
TOOL_PERMISSION_CHECKER_CREATED: Final[str] = "tool.permission.checker_created"
TOOL_PERMISSION_FILTERED: Final[str] = "tool.permission.filtered"

# ── File system tool events ──────────────────────────────────────
TOOL_FS_READ: Final[str] = "tool.fs.read"
TOOL_FS_WRITE: Final[str] = "tool.fs.write"
TOOL_FS_EDIT: Final[str] = "tool.fs.edit"
TOOL_FS_EDIT_NOT_FOUND: Final[str] = "tool.fs.edit_not_found"
TOOL_FS_LIST: Final[str] = "tool.fs.list"
TOOL_FS_DELETE: Final[str] = "tool.fs.delete"
TOOL_FS_PATH_VIOLATION: Final[str] = "tool.fs.path_violation"
TOOL_FS_BINARY_DETECTED: Final[str] = "tool.fs.binary_detected"
TOOL_FS_SIZE_EXCEEDED: Final[str] = "tool.fs.size_exceeded"
TOOL_FS_ERROR: Final[str] = "tool.fs.error"
TOOL_FS_STAT_FAILED: Final[str] = "tool.fs.stat_failed"
TOOL_FS_WORKSPACE_INVALID: Final[str] = "tool.fs.workspace_invalid"
TOOL_FS_PARENT_NOT_FOUND: Final[str] = "tool.fs.parent_not_found"
TOOL_FS_GLOB_REJECTED: Final[str] = "tool.fs.glob_rejected"
TOOL_FS_NOOP: Final[str] = "tool.fs.noop"

# ── Subprocess utility events ───────────────────────────────────
TOOL_SUBPROCESS_TRANSPORT_CLOSE_FAILED: Final[str] = (
    "tool.subprocess.transport_close_failed"
)
