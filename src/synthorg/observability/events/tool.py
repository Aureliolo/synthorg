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
TOOL_INVOKE_CONFIG_INVALID: Final[str] = "tool.invoke.config_invalid"
TOOL_PERMISSION_DENIED: Final[str] = "tool.permission.denied"
TOOL_PERMISSION_CHECKER_CREATED: Final[str] = "tool.permission.checker_created"
TOOL_PERMISSION_FILTERED: Final[str] = "tool.permission.filtered"

# ── Factory events ──────────────────────────────────────────────
TOOL_FACTORY_BUILT: Final[str] = "tool.factory.built"
TOOL_FACTORY_CONFIG_ENTRY: Final[str] = "tool.factory.config_entry"
TOOL_FACTORY_ERROR: Final[str] = "tool.factory.error"

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

# ── Security interception events ────────────────────────────────
TOOL_SECURITY_DENIED: Final[str] = "tool.security.denied"
TOOL_SECURITY_ESCALATED: Final[str] = "tool.security.escalated"
TOOL_OUTPUT_REDACTED: Final[str] = "tool.output.redacted"
TOOL_OUTPUT_WITHHELD: Final[str] = "tool.output.withheld"

# ── Subprocess utility events ───────────────────────────────────
TOOL_SUBPROCESS_TRANSPORT_CLOSE_FAILED: Final[str] = (
    "tool.subprocess.transport_close_failed"
)

# ── Invocation tracking events ─────────────────────────────────
TOOL_INVOCATION_RECORDED: Final[str] = "tool.invocation.recorded"
TOOL_INVOCATION_RECORD_FAILED: Final[str] = "tool.invocation.record_failed"
TOOL_INVOCATIONS_QUERIED: Final[str] = "tool.invocations.queried"
TOOL_INVOCATION_EVICTED: Final[str] = "tool.invocation.evicted"
TOOL_INVOCATION_TRACKER_CLEARED: Final[str] = "tool.invocation_tracker.cleared"
TOOL_INVOCATION_TIME_RANGE_INVALID: Final[str] = "tool.invocation.time_range.invalid"

# ── Progressive disclosure events ─────────────────────────────────
TOOL_L1_INJECTED: Final[str] = "tool.disclosure.l1_injected"
TOOL_L2_LOADED: Final[str] = "tool.disclosure.l2_loaded"
TOOL_L3_FETCHED: Final[str] = "tool.disclosure.l3_fetched"
TOOL_AUTO_UNLOADED: Final[str] = "tool.disclosure.auto_unloaded"
TOOL_DISCLOSURE_COLLISION: Final[str] = "tool.disclosure.collision"
TOOL_DISCLOSURE_LOAD_FAILED: Final[str] = "tool.disclosure.load_failed"
TOOL_DISCLOSURE_MANAGER_BOUND: Final[str] = "tool.disclosure.manager_bound"
TOOL_DISCLOSURE_MANAGER_NOT_BOUND: Final[str] = "tool.disclosure.manager_not_bound"
TOOL_DISCLOSURE_L1_SUMMARY_ERROR: Final[str] = "tool.disclosure.l1_summary_error"
TOOL_DISCLOSURE_TOKEN_SAVINGS: Final[str] = "tool.disclosure.token_savings"  # noqa: S105

# ── HTML parse guard events ────────────────────────────────────────
TOOL_HTML_PARSE_GAP_DETECTED: Final[str] = "tool.html_parse.gap_detected"
TOOL_HTML_PARSE_ERROR: Final[str] = "tool.html_parse.error"
TOOL_HTML_PARSE_XXE_DETECTED: Final[str] = "tool.html_parse.xxe_detected"

# ── Prompt-injection detection events ─────────────────────────────
TOOL_INJECTION_PATTERN_DETECTED: Final[str] = "tool.injection_pattern.detected"

# ── Registry integrity check events ──────────────────────────────
TOOL_REGISTRY_INTEGRITY_CHECK_START: Final[str] = "tool.registry.integrity.start"
TOOL_REGISTRY_INTEGRITY_VIOLATION: Final[str] = "tool.registry.integrity.violation"
TOOL_REGISTRY_INTEGRITY_CHECK_COMPLETE: Final[str] = "tool.registry.integrity.complete"

# ── Memory tool events ────────────────────────────────────────────
TOOL_MEMORY_AUGMENTATION_FAILED: Final[str] = "tool.memory.augmentation_failed"
