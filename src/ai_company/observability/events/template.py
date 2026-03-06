"""Template lifecycle event constants."""

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
TEMPLATE_PERSONALITY_PRESET_UNKNOWN: Final[str] = "template.personality_preset.unknown"
TEMPLATE_PASS1_FLOAT_FALLBACK: Final[str] = "template.pass1.float_fallback"
