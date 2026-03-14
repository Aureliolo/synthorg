"""Company templates: built-in presets and custom template loading.

Public API
----------
.. autosummary::
    load_template
    load_template_file
    list_templates
    list_builtin_templates
    render_template
    CompanyTemplate
    LoadedTemplate
    TemplateInfo
    TemplateMetadata
    TemplateVariable
    TemplateAgentConfig
    TemplateDepartmentConfig
    TemplateError
    TemplateInheritanceError
    TemplateNotFoundError
    TemplateRenderError
    TemplateValidationError
"""

from synthorg.templates.errors import (
    TemplateError,
    TemplateInheritanceError,
    TemplateNotFoundError,
    TemplateRenderError,
    TemplateValidationError,
)
from synthorg.templates.loader import (
    LoadedTemplate,
    TemplateInfo,
    list_builtin_templates,
    list_templates,
    load_template,
    load_template_file,
)
from synthorg.templates.renderer import render_template
from synthorg.templates.schema import (
    CompanyTemplate,
    TemplateAgentConfig,
    TemplateDepartmentConfig,
    TemplateMetadata,
    TemplateVariable,
)

__all__ = [
    "CompanyTemplate",
    "LoadedTemplate",
    "TemplateAgentConfig",
    "TemplateDepartmentConfig",
    "TemplateError",
    "TemplateInfo",
    "TemplateInheritanceError",
    "TemplateMetadata",
    "TemplateNotFoundError",
    "TemplateRenderError",
    "TemplateValidationError",
    "TemplateVariable",
    "list_builtin_templates",
    "list_templates",
    "load_template",
    "load_template_file",
    "render_template",
]
