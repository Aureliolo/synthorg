"""Company templates: built-in presets and custom template loading.

Public API
----------
.. autosummary::
    load_template
    load_template_file
    list_templates
    list_builtin_templates
    render_template
    validate_preset_references
    CompanyTemplate
    LoadedTemplate
    ModelMatch
    ModelRequirement
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

import threading
from typing import TYPE_CHECKING, Final

from synthorg.templates.errors import (
    TemplateError,
    TemplateInheritanceError,
    TemplateNotFoundError,
    TemplateRenderError,
    TemplateValidationError,
)

if TYPE_CHECKING:
    from synthorg.templates.loader import (
        LoadedTemplate,
        TemplateInfo,
        list_builtin_templates,
        list_templates,
        load_template,
        load_template_file,
    )
    from synthorg.templates.model_matcher import (
        ModelMatch,
        match_all_agents,
        match_model,
    )
    from synthorg.templates.model_requirements import (
        ModelRequirement,
        parse_model_requirement,
        resolve_model_requirement,
    )
    from synthorg.templates.pack_loader import (
        PackInfo,
        list_builtin_packs,
        list_packs,
        load_pack,
    )
    from synthorg.templates.presets import validate_preset_references
    from synthorg.templates.renderer import render_template
    from synthorg.templates.schema import (
        CompanyTemplate,
        TemplateAgentConfig,
        TemplateDepartmentConfig,
        TemplateMetadata,
        TemplateVariable,
    )

# name -> (module path, attribute) for PEP 562 lazy resolution. Only the
# ``errors`` leaf is imported eagerly; every other export reaches
# ``templates.schema`` -> ``engine.workflow.enums`` -> the ``engine`` hub, so an
# eager re-export made importing any ``templates.*`` leaf pull the whole engine
# and communication graph (ADR-0012).
_LAZY_EXPORTS: Final[dict[str, tuple[str, str]]] = {
    "CompanyTemplate": ("synthorg.templates.schema", "CompanyTemplate"),
    "TemplateAgentConfig": ("synthorg.templates.schema", "TemplateAgentConfig"),
    "TemplateDepartmentConfig": (
        "synthorg.templates.schema",
        "TemplateDepartmentConfig",
    ),
    "TemplateMetadata": ("synthorg.templates.schema", "TemplateMetadata"),
    "TemplateVariable": ("synthorg.templates.schema", "TemplateVariable"),
    "LoadedTemplate": ("synthorg.templates.loader", "LoadedTemplate"),
    "TemplateInfo": ("synthorg.templates.loader", "TemplateInfo"),
    "list_builtin_templates": (
        "synthorg.templates.loader",
        "list_builtin_templates",
    ),
    "list_templates": ("synthorg.templates.loader", "list_templates"),
    "load_template": ("synthorg.templates.loader", "load_template"),
    "load_template_file": ("synthorg.templates.loader", "load_template_file"),
    "ModelMatch": ("synthorg.templates.model_matcher", "ModelMatch"),
    "match_all_agents": ("synthorg.templates.model_matcher", "match_all_agents"),
    "match_model": ("synthorg.templates.model_matcher", "match_model"),
    "ModelRequirement": (
        "synthorg.templates.model_requirements",
        "ModelRequirement",
    ),
    "parse_model_requirement": (
        "synthorg.templates.model_requirements",
        "parse_model_requirement",
    ),
    "resolve_model_requirement": (
        "synthorg.templates.model_requirements",
        "resolve_model_requirement",
    ),
    "PackInfo": ("synthorg.templates.pack_loader", "PackInfo"),
    "list_builtin_packs": ("synthorg.templates.pack_loader", "list_builtin_packs"),
    "list_packs": ("synthorg.templates.pack_loader", "list_packs"),
    "load_pack": ("synthorg.templates.pack_loader", "load_pack"),
    "validate_preset_references": (
        "synthorg.templates.presets",
        "validate_preset_references",
    ),
    "render_template": ("synthorg.templates.renderer", "render_template"),
}

_LAZY_EXPORT_LOCK: Final[threading.Lock] = threading.Lock()


def __getattr__(name: str) -> object:
    """Resolve and cache a lazily-exported symbol on first access (PEP 562).

    Returns:
        The resolved (and now cached) export object for ``name``.

    Raises:
        AttributeError: When ``name`` is not a known lazy export.
    """
    if name not in _LAZY_EXPORTS:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    import importlib  # noqa: PLC0415

    if name in globals():
        return globals()[name]
    module_path, attr = _LAZY_EXPORTS[name]
    # Resolve the import OUTSIDE the lock: importing the target runs arbitrary
    # module-level code that can re-enter this hub (the import cycles this lazy
    # machinery exists to break), so holding a non-reentrant lock across the
    # import would risk a same-thread self-deadlock or a cross-hub lock-order
    # inversion. Python's per-module import lock already dedups the work, so a
    # racing first access at worst resolves the idempotent value twice;
    # ``setdefault`` keeps a single cached object.
    value = getattr(importlib.import_module(module_path), attr)
    with _LAZY_EXPORT_LOCK:
        return globals().setdefault(name, value)


def __dir__() -> list[str]:
    """Include the lazily-exported names in ``dir()`` / autocomplete.

    Returns:
        The sorted list of public export names.
    """
    return sorted(__all__)


__all__ = [
    "CompanyTemplate",
    "LoadedTemplate",
    "ModelMatch",
    "ModelRequirement",
    "PackInfo",
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
    "list_builtin_packs",
    "list_builtin_templates",
    "list_packs",
    "list_templates",
    "load_pack",
    "load_template",
    "load_template_file",
    "match_all_agents",
    "match_model",
    "parse_model_requirement",
    "render_template",
    "resolve_model_requirement",
    "validate_preset_references",
]
