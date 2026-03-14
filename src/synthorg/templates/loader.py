"""Template loading from built-in, user directory, and file-system sources.

Implements a two-pass loading strategy:

- **Pass 1**: YAML-parse the template to extract metadata and the
  ``variables`` section (which uses plain YAML, no Jinja2).
- **Pass 2**: Performed later by the renderer — Jinja2-renders the raw
  YAML text, then YAML-parses the result.

Both are returned bundled as a :class:`LoadedTemplate` dataclass.
"""

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import yaml
from pydantic import ValidationError

from synthorg.config.errors import ConfigLocation
from synthorg.observability import get_logger
from synthorg.observability.events.template import (
    TEMPLATE_BUILTIN_DEFECT,
    TEMPLATE_LIST_SKIP_INVALID,
    TEMPLATE_LOAD_ERROR,
    TEMPLATE_LOAD_INVALID_NAME,
    TEMPLATE_LOAD_NOT_FOUND,
    TEMPLATE_LOAD_PARSE_ERROR,
    TEMPLATE_LOAD_READ_ERROR,
    TEMPLATE_LOAD_START,
    TEMPLATE_LOAD_STRUCTURE_ERROR,
    TEMPLATE_LOAD_SUCCESS,
    TEMPLATE_PASS1_FLOAT_FALLBACK,
)
from synthorg.templates.errors import (
    TemplateNotFoundError,
    TemplateRenderError,
    TemplateValidationError,
)
from synthorg.templates.schema import CompanyTemplate

logger = get_logger(__name__)

_USER_TEMPLATES_DIR = Path.home() / ".synthorg" / "templates"

BUILTIN_TEMPLATES: MappingProxyType[str, str] = MappingProxyType(
    {
        "solo_founder": "solo_founder.yaml",
        "startup": "startup.yaml",
        "dev_shop": "dev_shop.yaml",
        "product_team": "product_team.yaml",
        "agency": "agency.yaml",
        "full_company": "full_company.yaml",
        "research_lab": "research_lab.yaml",
    }
)


@dataclass(frozen=True)
class TemplateInfo:
    """Summary information about an available template.

    Attributes:
        name: Template identifier (e.g. ``"startup"``).
        display_name: Human-readable display name.
        description: Short description.
        source: Where the template was found (``"builtin"`` or ``"user"``).
    """

    name: str
    display_name: str
    description: str
    source: Literal["builtin", "user"]


@dataclass(frozen=True)
class LoadedTemplate:
    """Result of loading a template: structured data + raw text.

    Attributes:
        template: Validated ``CompanyTemplate`` from Pass 1.
        raw_yaml: Raw YAML text for Pass 2 (Jinja2 rendering).
        source_name: Label for error messages.
    """

    template: CompanyTemplate
    raw_yaml: str
    source_name: str


def list_templates() -> tuple[TemplateInfo, ...]:
    """Return all available templates (user directory + built-in).

    User templates override built-in ones. Sorted by name.

    Returns:
        Sorted tuple of :class:`TemplateInfo` objects.
    """
    seen: dict[str, TemplateInfo] = {}

    # User templates (higher priority).
    _collect_user_templates(seen)

    # Built-in templates (lower priority).
    for name in sorted(BUILTIN_TEMPLATES):
        if name not in seen:
            try:
                loaded = _load_builtin(name)
                meta = loaded.template.metadata
                seen[name] = TemplateInfo(
                    name=name,
                    display_name=meta.name,
                    description=meta.description,
                    source="builtin",
                )
            except (TemplateRenderError, TemplateValidationError, OSError) as exc:
                logger.exception(
                    TEMPLATE_BUILTIN_DEFECT,
                    template_name=name,
                    error=str(exc),
                )

    return tuple(info for _, info in sorted(seen.items()))


def _collect_user_templates(seen: dict[str, TemplateInfo]) -> None:
    """Scan user templates directory and populate *seen*."""
    if not _USER_TEMPLATES_DIR.is_dir():
        return
    for path in sorted(p for p in _USER_TEMPLATES_DIR.glob("*.yaml") if p.is_file()):
        name = path.stem
        try:
            loaded = _load_from_file(path)
            meta = loaded.template.metadata
            seen[name] = TemplateInfo(
                name=name,
                display_name=meta.name,
                description=meta.description,
                source="user",
            )
        except (TemplateRenderError, TemplateValidationError, OSError) as exc:
            logger.warning(
                TEMPLATE_LIST_SKIP_INVALID,
                template_path=str(path),
                error=str(exc),
            )


def list_builtin_templates() -> tuple[str, ...]:
    """Return names of all built-in templates.

    Returns:
        Sorted tuple of built-in template names.
    """
    return tuple(sorted(BUILTIN_TEMPLATES))


def load_template(name: str) -> LoadedTemplate:
    """Load a template by name: user directory first, then builtins.

    Args:
        name: Template name (e.g. ``"startup"``).

    Returns:
        :class:`LoadedTemplate` with validated data and raw YAML.

    Raises:
        TemplateNotFoundError: If no template with *name* exists.
    """
    name_clean = name.strip().lower()
    logger.debug(TEMPLATE_LOAD_START, template_name=name_clean)

    # Sanitize to prevent path traversal (OS-independent).
    if "/" in name_clean or "\\" in name_clean or ".." in name_clean:
        msg = f"Invalid template name {name!r}: must not contain path separators"
        logger.warning(TEMPLATE_LOAD_INVALID_NAME, template_name=name)
        raise TemplateNotFoundError(
            msg,
            locations=(ConfigLocation(file_path=f"<template:{name}>"),),
        )

    # Try user directory first.
    if _USER_TEMPLATES_DIR.is_dir():
        user_path = _USER_TEMPLATES_DIR / f"{name_clean}.yaml"
        if user_path.is_file():
            result = _load_from_file(user_path)
            logger.debug(
                TEMPLATE_LOAD_SUCCESS,
                template_name=name_clean,
                source="user",
            )
            return result

    # Fall back to builtins.
    if name_clean in BUILTIN_TEMPLATES:
        result = _load_builtin(name_clean)
        logger.debug(
            TEMPLATE_LOAD_SUCCESS,
            template_name=name_clean,
            source="builtin",
        )
        return result

    available = list_builtin_templates()
    logger.error(
        TEMPLATE_LOAD_ERROR,
        template_name=name,
        available=list(available),
    )
    msg = f"Unknown template {name!r}. Available: {list(available)}"
    raise TemplateNotFoundError(
        msg,
        locations=(ConfigLocation(file_path=f"<template:{name}>"),),
    )


def load_template_file(path: Path | str) -> LoadedTemplate:
    """Load a template from an explicit file path.

    Args:
        path: Path to the template YAML file.

    Returns:
        :class:`LoadedTemplate` with validated data and raw YAML.

    Raises:
        TemplateNotFoundError: If the file does not exist.
        TemplateValidationError: If validation fails.
    """
    path = Path(path)
    if not path.is_file():
        msg = f"Template file not found: {path}"
        logger.warning(TEMPLATE_LOAD_NOT_FOUND, path=str(path))
        raise TemplateNotFoundError(
            msg,
            locations=(ConfigLocation(file_path=str(path)),),
        )
    return _load_from_file(path)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _load_builtin(name: str) -> LoadedTemplate:
    """Load a built-in template by name."""
    filename = BUILTIN_TEMPLATES.get(name)
    if filename is None:
        msg = f"Unknown built-in template: {name!r}"
        logger.warning(TEMPLATE_LOAD_NOT_FOUND, template_name=name)
        raise TemplateNotFoundError(
            msg,
            locations=(ConfigLocation(file_path=f"<builtin:{name}>"),),
        )
    source_name = f"<builtin:{name}>"
    try:
        ref = resources.files("synthorg.templates.builtins") / filename
        yaml_text = ref.read_text(encoding="utf-8")
    except (OSError, ImportError, TypeError) as exc:
        msg = f"Failed to read built-in template resource {filename!r}: {exc}"
        logger.exception(TEMPLATE_LOAD_READ_ERROR, source=source_name, error=str(exc))
        raise TemplateRenderError(
            msg,
            locations=(ConfigLocation(file_path=source_name),),
        ) from exc
    template = _parse_template_yaml(yaml_text, source_name=source_name)
    return LoadedTemplate(
        template=template,
        raw_yaml=yaml_text,
        source_name=source_name,
    )


def _load_from_file(path: Path) -> LoadedTemplate:
    """Load a template from a file path.

    Raises:
        TemplateRenderError: If the file cannot be read or YAML
            parsing fails.
        TemplateValidationError: If validation fails.
    """
    source_name = str(path)
    try:
        yaml_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"Unable to read template file: {path}"
        logger.warning(TEMPLATE_LOAD_READ_ERROR, path=str(path), error=str(exc))
        raise TemplateRenderError(
            msg,
            locations=(ConfigLocation(file_path=source_name),),
        ) from exc
    except UnicodeDecodeError as exc:
        msg = f"Template file is not valid UTF-8: {path}"
        logger.warning(TEMPLATE_LOAD_READ_ERROR, path=str(path), error=str(exc))
        raise TemplateRenderError(
            msg,
            locations=(ConfigLocation(file_path=source_name),),
        ) from exc
    template = _parse_template_yaml(yaml_text, source_name=source_name)
    return LoadedTemplate(
        template=template,
        raw_yaml=yaml_text,
        source_name=source_name,
    )


def _strip_jinja2_for_pass1(yaml_text: str) -> str:
    """Replace Jinja2 expressions with YAML-safe placeholders for Pass 1.

    Pass 1 only extracts metadata and the ``variables`` section (which
    must be plain YAML).  The rest of the template may contain unquoted
    Jinja2 expressions (``{{ }}``, ``{% %}``, ``{# #}``) that are
    invalid YAML.  This function replaces them with safe placeholders
    so that ``yaml.safe_load`` succeeds.

    Args:
        yaml_text: Raw template YAML with possible Jinja2 expressions.

    Returns:
        YAML text with Jinja2 expressions replaced by safe strings.
    """
    # Replace {{ ... }} with a bare placeholder (no extra quotes,
    # so it works both inside quoted strings and unquoted values).
    text = re.sub(r"\{\{.*?\}\}", "__JINJA2__", yaml_text)
    # Remove {% ... %} block tags (lines containing only a tag are removed).
    text = re.sub(r"\{%.*?%\}", "", text)
    # Remove {# ... #} comments.
    return re.sub(r"\{#.*?#\}", "", text)


def _parse_template_yaml(
    yaml_text: str,
    *,
    source_name: str,
) -> CompanyTemplate:
    """Parse a template YAML string into a CompanyTemplate (Pass 1).

    Args:
        yaml_text: Raw YAML content.
        source_name: Label for error messages.

    Returns:
        Validated :class:`CompanyTemplate`.

    Raises:
        TemplateRenderError: If YAML parsing fails.
        TemplateValidationError: If the structure fails validation.
    """
    safe_text = _strip_jinja2_for_pass1(yaml_text)
    try:
        data = yaml.safe_load(safe_text)
    except yaml.YAMLError as exc:
        msg = f"Template YAML syntax error in {source_name}: {exc}"
        logger.warning(TEMPLATE_LOAD_PARSE_ERROR, source=source_name, error=str(exc))
        raise TemplateRenderError(
            msg,
            locations=(ConfigLocation(file_path=source_name),),
        ) from exc

    template_data = _validate_template_structure(data, source_name)
    try:
        normalized = _normalize_template_data(template_data)
        return CompanyTemplate(**normalized)
    except (ValidationError, ValueError, TypeError) as exc:
        msg = f"Template validation failed for {source_name}: {exc}"
        logger.warning(TEMPLATE_LOAD_PARSE_ERROR, source=source_name, error=str(exc))
        raise TemplateValidationError(
            msg,
            locations=(ConfigLocation(file_path=source_name),),
        ) from exc


def _validate_template_structure(
    data: Any,
    source_name: str,
) -> dict[str, Any]:
    """Validate top-level YAML structure has a dict 'template' key.

    Raises:
        TemplateValidationError: If structure is invalid.
    """
    if not isinstance(data, dict) or "template" not in data:
        msg = f"Template YAML must have a top-level 'template' key in {source_name}"
        logger.warning(TEMPLATE_LOAD_STRUCTURE_ERROR, source=source_name, error=msg)
        raise TemplateValidationError(
            msg,
            locations=(ConfigLocation(file_path=source_name),),
        )
    template_data = data["template"]
    if not isinstance(template_data, dict):
        msg = f"Template 'template' key must map to an object in {source_name}"
        logger.warning(TEMPLATE_LOAD_STRUCTURE_ERROR, source=source_name, error=msg)
        raise TemplateValidationError(
            msg,
            locations=(ConfigLocation(file_path=source_name),),
        )
    return template_data


def _normalize_template_data(data: dict[str, Any]) -> dict[str, Any]:
    """Transform raw YAML template data into CompanyTemplate kwargs.

    Bridges the human-friendly flat YAML format and the nested Pydantic
    model shape.

    Args:
        data: The dict under the top-level ``template`` key.

    Returns:
        Dict suitable for ``CompanyTemplate(**result)``.
    """
    company = data.get("company")
    if company is None:
        company = {}
    elif not isinstance(company, dict):
        msg = "Template field 'template.company' must be a mapping"
        logger.warning(
            TEMPLATE_LOAD_STRUCTURE_ERROR,
            source="template.company",
            error=msg,
        )
        raise TypeError(msg)

    metadata: dict[str, Any] = {
        "description": data.get("description", ""),
        "version": data.get("version", "1.0.0"),
        "company_type": company.get("type", "custom"),
        "tags": tuple(data.get("tags", ())),
    }
    if "name" in data:
        metadata["name"] = data["name"]
    if "min_agents" in data:
        metadata["min_agents"] = data["min_agents"]
    if "max_agents" in data:
        metadata["max_agents"] = data["max_agents"]

    result: dict[str, Any] = {
        "metadata": metadata,
        "variables": data.get("variables", ()),
        "agents": data.get("agents", ()),
        "departments": data.get("departments", ()),
        "workflow": data.get("workflow", "agile_kanban"),
        "communication": data.get("communication", "hybrid"),
        "budget_monthly": _to_float(company.get("budget_monthly", 50.0)),
        "autonomy": _to_float(company.get("autonomy", 0.5)),
        "workflow_handoffs": data.get("workflow_handoffs", ()),
        "escalation_paths": data.get("escalation_paths", ()),
    }
    if "extends" in data:
        result["extends"] = data["extends"]
    return result


def _to_float(value: Any) -> float:
    """Coerce a value to float for Pass 1 normalization.

    Returns ``0.0`` for values that cannot be converted (e.g. Jinja2
    placeholders like ``__JINJA2__``) since the real value will be
    resolved in Pass 2.

    Args:
        value: Raw value from YAML (may be str, int, float, or
            ``None``).

    Returns:
        Float value, or ``0.0`` for ``None`` or unconvertible strings
        (typically Jinja2 placeholders).
    """
    if value is None:
        return 0.0
    try:
        return float(value)
    except TypeError, ValueError:
        logger.debug(
            TEMPLATE_PASS1_FLOAT_FALLBACK,
            value=repr(value),
        )
        return 0.0
