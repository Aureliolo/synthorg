"""Template event constants."""

from typing import Final

TEMPLATE_LOAD_START: Final[str] = "template.load.start"
TEMPLATE_LOAD_SUCCESS: Final[str] = "template.load.success"
TEMPLATE_LOAD_ERROR: Final[str] = "template.load.error"
TEMPLATE_LIST_SKIP_INVALID: Final[str] = "template.list.skip_invalid"
TEMPLATE_BUILTIN_DEFECT: Final[str] = "template.builtin.defect"
TEMPLATE_RENDER_START: Final[str] = "template.render.start"
TEMPLATE_RENDER_SUCCESS: Final[str] = "template.render.success"
TEMPLATE_RENDER_VARIABLE_ERROR: Final[str] = "template.render.variable_error"
TEMPLATE_RENDER_JINJA2_ERROR: Final[str] = "template.render.jinja2_error"
TEMPLATE_RENDER_YAML_ERROR: Final[str] = "template.render.yaml_error"
TEMPLATE_RENDER_VALIDATION_ERROR: Final[str] = "template.render.validation_error"
TEMPLATE_PERSONALITY_PRESET_INVALID: Final[str] = "template.personality_preset.invalid"
TEMPLATE_PERSONALITY_PRESET_UNKNOWN: Final[str] = "template.personality_preset.unknown"
TEMPLATE_PRESET_RESOLVED_CUSTOM: Final[str] = (
    "template.personality_preset.resolved_custom"
)
TEMPLATE_PASS1_FLOAT_FALLBACK: Final[str] = "template.pass1.float_fallback"
TEMPLATE_INHERIT_RESOLVE_START: Final[str] = "template.inherit.resolve_start"
TEMPLATE_INHERIT_RESOLVE_SUCCESS: Final[str] = "template.inherit.resolve_success"
TEMPLATE_INHERIT_CIRCULAR: Final[str] = "template.inherit.circular"
TEMPLATE_INHERIT_DEPTH_EXCEEDED: Final[str] = "template.inherit.depth_exceeded"
TEMPLATE_INHERIT_MERGE: Final[str] = "template.inherit.merge"
TEMPLATE_INHERIT_MERGE_ERROR: Final[str] = "template.inherit.merge_error"
TEMPLATE_RENDER_TYPE_ERROR: Final[str] = "template.render.type_error"
TEMPLATE_LOAD_NOT_FOUND: Final[str] = "template.load.not_found"
TEMPLATE_LOAD_READ_ERROR: Final[str] = "template.load.read_error"
TEMPLATE_LOAD_PARSE_ERROR: Final[str] = "template.load.parse_error"
TEMPLATE_LOAD_STRUCTURE_ERROR: Final[str] = "template.load.structure_error"
TEMPLATE_LOAD_INVALID_NAME: Final[str] = "template.load.invalid_name"
TEMPLATE_SCHEMA_VALIDATION_ERROR: Final[str] = "template.schema.validation_error"

# Model requirement parsing and resolution
TEMPLATE_MODEL_REQUIREMENT_INVALID: Final[str] = "template.model_requirement.invalid"
TEMPLATE_MODEL_REQUIREMENT_PARSED: Final[str] = "template.model_requirement.parsed"
TEMPLATE_MODEL_REQUIREMENT_RESOLVED: Final[str] = "template.model_requirement.resolved"

# Name generation
TEMPLATE_NAME_GEN_FAKER_ERROR: Final[str] = "template.name_generation.faker_error"

# Locale resolution
TEMPLATE_LOCALES_DROPPED_INVALID: Final[str] = "template.locales.dropped_invalid"

# Workflow config
TEMPLATE_WORKFLOW_CONFIG_UNKNOWN_KEY: Final[str] = (
    "template.workflow_config.unknown_key"
)

# Model matching
TEMPLATE_MODEL_MATCH_SUCCESS: Final[str] = "template.model_match.success"
TEMPLATE_MODEL_MATCH_FAILED: Final[str] = "template.model_match.failed"
TEMPLATE_MODEL_MATCH_SKIPPED: Final[str] = "template.model_match.skipped"
# Emitted at DEBUG when the requested tier had no models but the
# fallback path (first available model with score 0) succeeded.
# Fallback is the documented contract per the design page; treating
# it as a WARNING produced ~8 noisy log lines during every clean
# setup wizard run (issue #1666 B-5). Operators only see WARNING
# now when the fallback ALSO fails (no models available at all),
# which after the wizard provider gate should not fire in practice.
TEMPLATE_MODEL_MATCH_FALLBACK: Final[str] = "template.model_match.fallback"

# Template packs
TEMPLATE_PACK_LOAD_START: Final[str] = "template.pack.load.start"
TEMPLATE_PACK_LOAD_SUCCESS: Final[str] = "template.pack.load.success"
TEMPLATE_PACK_LOAD_NOT_FOUND: Final[str] = "template.pack.load.not_found"
TEMPLATE_PACK_LIST: Final[str] = "template.pack.list"
TEMPLATE_PACK_MERGE_START: Final[str] = "template.pack.merge.start"
TEMPLATE_PACK_MERGE_SUCCESS: Final[str] = "template.pack.merge.success"
TEMPLATE_PACK_APPLY_START: Final[str] = "template.pack.apply.start"
TEMPLATE_PACK_APPLY_SUCCESS: Final[str] = "template.pack.apply.success"
TEMPLATE_PACK_APPLY_ERROR: Final[str] = "template.pack.apply.error"
TEMPLATE_PACK_APPLY_DEPT_SKIPPED: Final[str] = "template.pack.apply.dept_skipped"
TEMPLATE_PACK_BUDGET_REBALANCED: Final[str] = "template.pack.budget_rebalanced"
TEMPLATE_PACK_BUDGET_REJECTED: Final[str] = "template.pack.budget_rejected"
TEMPLATE_PACK_CIRCULAR: Final[str] = "template.pack.circular"
TEMPLATE_PACK_SETTING_NOT_FOUND: Final[str] = "template.pack.setting_not_found"

# MCP audit events
TEMPLATE_PACK_INSTALLED_VIA_MCP: Final[str] = "template.pack.installed_via_mcp"
TEMPLATE_PACK_UNINSTALLED_VIA_MCP: Final[str] = "template.pack.uninstalled_via_mcp"
