"""Template rendering: Jinja2 substitution + validation to RootConfig.

Implements the second pass of the two-pass rendering pipeline:

1. Collect user variables + defaults from the ``CompanyTemplate``.
2. Render the raw YAML text through a Jinja2 ``SandboxedEnvironment``.
3. YAML-parse the rendered text.
4. Build a ``RootConfig``-compatible dict and validate.
"""

from typing import TYPE_CHECKING, Any

import yaml
from jinja2 import TemplateError as Jinja2TemplateError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import ValidationError

from ai_company.config.defaults import default_config_dict
from ai_company.config.errors import ConfigLocation
from ai_company.config.schema import RootConfig
from ai_company.config.utils import deep_merge, to_float
from ai_company.core.agent import PersonalityConfig
from ai_company.observability import get_logger
from ai_company.observability.events.template import (
    TEMPLATE_RENDER_JINJA2_ERROR,
    TEMPLATE_RENDER_START,
    TEMPLATE_RENDER_SUCCESS,
    TEMPLATE_RENDER_VALIDATION_ERROR,
    TEMPLATE_RENDER_VARIABLE_ERROR,
    TEMPLATE_RENDER_YAML_ERROR,
)
from ai_company.templates.errors import (
    TemplateRenderError,
    TemplateValidationError,
)
from ai_company.templates.presets import (
    generate_auto_name,
    get_personality_preset,
)

# Placeholder provider name resolved by the engine at startup.
_DEFAULT_PROVIDER = "default"

# Default department when not specified in template agent config.
_DEFAULT_DEPARTMENT = "engineering"

if TYPE_CHECKING:
    from ai_company.templates.loader import LoadedTemplate
    from ai_company.templates.schema import CompanyTemplate

logger = get_logger(__name__)


def render_template(
    loaded: LoadedTemplate,
    variables: dict[str, Any] | None = None,
) -> RootConfig:
    """Render a loaded template into a validated RootConfig.

    Args:
        loaded: :class:`LoadedTemplate` from the loader.
        variables: User-supplied variable values (overrides defaults).

    Returns:
        Validated, frozen :class:`RootConfig`.

    Raises:
        TemplateRenderError: If rendering fails.
        TemplateValidationError: If validation fails.
    """
    logger.info(
        TEMPLATE_RENDER_START,
        source_name=loaded.source_name,
    )
    template = loaded.template
    vars_dict = _collect_variables(template, variables or {})

    # Jinja2-render the raw YAML (Pass 2).
    rendered_text = _render_jinja2(
        loaded.raw_yaml,
        vars_dict,
        source_name=loaded.source_name,
    )

    # Parse the rendered YAML.
    rendered_data = _parse_rendered_yaml(rendered_text, loaded.source_name)

    # Build RootConfig dict from the rendered data.
    config_dict = _build_config_dict(rendered_data, template, vars_dict)

    # Merge with defaults and validate.
    merged = deep_merge(default_config_dict(), config_dict)
    result = _validate_as_root_config(merged, loaded.source_name)
    logger.info(
        TEMPLATE_RENDER_SUCCESS,
        source_name=loaded.source_name,
    )
    return result


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _collect_variables(
    template: CompanyTemplate,
    user_vars: dict[str, Any],
) -> dict[str, Any]:
    """Merge user variables with template defaults.

    Args:
        template: Template with variable declarations.
        user_vars: User-supplied values.

    Returns:
        Complete variable dict.

    Raises:
        TemplateRenderError: If a required variable is missing.
    """
    result: dict[str, Any] = {}
    for var in template.variables:
        if var.name in user_vars:
            result[var.name] = user_vars[var.name]
        elif var.default is not None:
            result[var.name] = var.default
        elif var.required:
            logger.error(
                TEMPLATE_RENDER_VARIABLE_ERROR,
                variable=var.name,
            )
            msg = f"Required template variable {var.name!r} was not provided"
            raise TemplateRenderError(msg)
        # Optional vars with no default and no user value are omitted;
        # the Jinja2 template will get ``Undefined`` for them.

    # Pass through extra user vars not declared in the template.
    for key, value in user_vars.items():
        if key not in result:
            result[key] = value

    return result


def _create_jinja_env() -> SandboxedEnvironment:
    """Create a sandboxed Jinja2 environment with custom filters.

    Returns:
        Configured :class:`SandboxedEnvironment`.
    """
    env = SandboxedEnvironment(
        keep_trailing_newline=True,
    )
    # ``auto`` filter: converts falsy values to empty string, which
    # triggers auto-name generation downstream (empty names are
    # detected by ``_expand_agents``).
    env.filters["auto"] = lambda value: value or ""
    return env


def _render_jinja2(
    raw_yaml: str,
    variables: dict[str, Any],
    *,
    source_name: str,
) -> str:
    """Render raw YAML text through Jinja2 with given variables.

    Args:
        raw_yaml: Template YAML text with Jinja2 expressions.
        variables: Collected variable values.
        source_name: Label for error messages.

    Returns:
        Rendered YAML text with all expressions resolved.

    Raises:
        TemplateRenderError: If Jinja2 rendering fails.
    """
    env = _create_jinja_env()
    try:
        jinja_template = env.from_string(raw_yaml)
        return jinja_template.render(**variables)
    except Jinja2TemplateError as exc:
        logger.exception(
            TEMPLATE_RENDER_JINJA2_ERROR,
            source_name=source_name,
            error=str(exc),
        )
        msg = f"Jinja2 rendering failed for {source_name}: {exc}"
        raise TemplateRenderError(
            msg,
            locations=(ConfigLocation(file_path=source_name),),
        ) from exc


def _parse_rendered_yaml(
    rendered_text: str,
    source_name: str,
) -> dict[str, Any]:
    """Parse the Jinja2-rendered YAML text.

    Args:
        rendered_text: YAML text with all Jinja2 expressions resolved.
        source_name: Label for error messages.

    Returns:
        Parsed dict from the ``template`` key.

    Raises:
        TemplateRenderError: If YAML parsing fails.
    """
    try:
        data = yaml.safe_load(rendered_text)
    except yaml.YAMLError as exc:
        logger.exception(
            TEMPLATE_RENDER_YAML_ERROR,
            source_name=source_name,
            error=str(exc),
        )
        msg = f"Rendered template YAML is invalid for {source_name}: {exc}"
        raise TemplateRenderError(
            msg,
            locations=(ConfigLocation(file_path=source_name),),
        ) from exc

    if not isinstance(data, dict) or "template" not in data:
        msg = f"Rendered template missing 'template' key: {source_name}"
        raise TemplateRenderError(
            msg,
            locations=(ConfigLocation(file_path=source_name),),
        )

    template_data = data["template"]
    if not isinstance(template_data, dict):
        msg = f"Rendered template 'template' key must be a mapping: {source_name}"
        raise TemplateRenderError(
            msg,
            locations=(ConfigLocation(file_path=source_name),),
        )
    return template_data


def _build_config_dict(
    rendered_data: dict[str, Any],
    template: CompanyTemplate,
    variables: dict[str, Any],
) -> dict[str, Any]:
    """Build a RootConfig-compatible dict from rendered template data.

    Args:
        rendered_data: Parsed dict from the rendered YAML.
        template: Original template metadata (for fallback values).
        variables: Collected variables.

    Returns:
        Dict suitable for ``RootConfig(**deep_merge(defaults, result))``.
    """
    company = rendered_data.get("company", {})
    if company is None:
        company = {}
    if not isinstance(company, dict):
        msg = "Rendered template 'company' must be a mapping"
        raise TemplateRenderError(msg)

    company_name = variables.get(
        "company_name",
        template.metadata.name,
    )

    agents = _expand_agents(_validate_list(rendered_data, "agents"))
    departments = _build_departments(_validate_list(rendered_data, "departments"))

    autonomy, budget_monthly = _extract_numeric_config(company, template)

    result: dict[str, Any] = {
        "company_name": company_name,
        "company_type": company.get("type", template.metadata.company_type.value),
        "agents": agents,
        "departments": departments,
        "config": {
            "autonomy": autonomy,
            "budget_monthly": budget_monthly,
            "communication_pattern": rendered_data.get(
                "communication",
                template.communication,
            ),
        },
    }

    for key in ("workflow_handoffs", "escalation_paths"):
        if rendered_data.get(key):
            result[key] = _validate_list(rendered_data, key)

    return result


def _validate_list(
    rendered_data: dict[str, Any],
    key: str,
) -> list[dict[str, Any]]:
    """Extract and validate a list field from rendered data."""
    raw = rendered_data.get(key, [])
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        msg = f"Rendered template {key!r} must be a list"
        raise TemplateRenderError(msg)
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            msg = (
                f"Rendered template {key!r}[{i}] must be a "
                f"mapping, got {type(item).__name__}"
            )
            raise TemplateRenderError(msg)
    return raw


def _extract_numeric_config(
    company: dict[str, Any],
    template: CompanyTemplate,
) -> tuple[float, float]:
    """Extract autonomy and budget_monthly as floats."""
    source_name = template.metadata.name
    try:
        autonomy = to_float(
            company.get("autonomy", template.autonomy),
            field_name="autonomy",
        )
        budget_monthly = to_float(
            company.get("budget_monthly", template.budget_monthly),
            field_name="budget_monthly",
        )
    except ValueError as exc:
        msg = f"Invalid numeric value in rendered template {source_name!r}: {exc}"
        raise TemplateRenderError(msg) from exc
    return autonomy, budget_monthly


def _expand_agents(
    raw_agents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Expand template agent dicts into AgentConfig-compatible dicts.

    Args:
        raw_agents: List of agent dicts from rendered YAML.

    Returns:
        List of dicts suitable for ``AgentConfig`` construction.
    """
    expanded: list[dict[str, Any]] = []
    used_names: set[str] = set()

    for idx, agent in enumerate(raw_agents):
        expanded.append(_expand_single_agent(agent, idx, used_names))

    return expanded


def _expand_single_agent(
    agent: dict[str, Any],
    idx: int,
    used_names: set[str],
) -> dict[str, Any]:
    """Expand a single template agent dict."""
    role = agent.get("role", "Agent")
    name = str(agent.get("name", "")).strip()

    if not name or name.startswith("{{"):
        name = generate_auto_name(role, seed=idx)

    base_name = name
    counter = 2
    while name in used_names:
        name = f"{base_name} {counter}"
        counter += 1
    used_names.add(name)

    agent_dict: dict[str, Any] = {
        "name": name,
        "role": role,
        "department": agent.get("department", _DEFAULT_DEPARTMENT),
        "level": agent.get("level", "mid"),
    }

    inline_personality = agent.get("personality")
    preset_name = agent.get("personality_preset")
    if inline_personality and isinstance(inline_personality, dict):
        _validate_inline_personality(inline_personality, name)
        agent_dict["personality"] = inline_personality
    elif preset_name:
        try:
            agent_dict["personality"] = get_personality_preset(preset_name)
        except KeyError as exc:
            msg = f"Unknown personality preset {preset_name!r} for agent {name!r}"
            raise TemplateRenderError(msg) from exc

    model_tier = agent.get("model", "medium")
    agent_dict["model"] = {"provider": _DEFAULT_PROVIDER, "model_id": model_tier}
    return agent_dict


def _validate_inline_personality(
    personality: dict[str, Any],
    agent_name: str,
) -> None:
    """Eagerly validate an inline personality dict.

    Args:
        personality: Raw personality dict from template YAML.
        agent_name: Agent name for error context.

    Raises:
        TemplateRenderError: If the dict is not valid for PersonalityConfig.
    """
    try:
        PersonalityConfig(**personality)
    except Exception as exc:
        msg = f"Invalid inline personality for agent {agent_name!r}: {exc}"
        raise TemplateRenderError(msg) from exc


def _build_departments(
    raw_depts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build RootConfig-compatible department dicts.

    Args:
        raw_depts: List of department dicts from rendered YAML.

    Returns:
        List of dicts suitable for ``Department`` construction.
    """
    departments: list[dict[str, Any]] = []
    for dept in raw_depts:
        try:
            budget_pct = to_float(
                dept.get("budget_percent", 0.0),
                field_name=f"departments[{dept.get('name', '')}].budget_percent",
            )
        except ValueError as exc:
            msg = f"Invalid department budget value: {exc}"
            raise TemplateRenderError(msg) from exc
        dept_dict: dict[str, Any] = {
            "name": dept.get("name", ""),
            "head": dept.get("head_role", dept.get("name", "")),
            "budget_percent": budget_pct,
        }
        reporting_lines = dept.get("reporting_lines")
        if reporting_lines:
            if not isinstance(reporting_lines, list):
                dept_name = dept.get("name", "")
                msg = f"Department {dept_name!r} 'reporting_lines' must be a list"
                raise TemplateRenderError(msg)
            dept_dict["reporting_lines"] = reporting_lines
        policies = dept.get("policies")
        if policies:
            if not isinstance(policies, dict):
                dept_name = dept.get("name", "")
                msg = f"Department {dept_name!r} 'policies' must be a mapping"
                raise TemplateRenderError(msg)
            dept_dict["policies"] = policies
        departments.append(dept_dict)
    return departments


def _validate_as_root_config(
    merged: dict[str, Any],
    source_name: str,
) -> RootConfig:
    """Validate a merged config dict as RootConfig.

    Args:
        merged: Merged config dict.
        source_name: Label for error messages.

    Returns:
        Validated, frozen :class:`RootConfig`.

    Raises:
        TemplateValidationError: If validation fails.
    """
    try:
        return RootConfig(**merged)
    except ValidationError as exc:
        field_errors: list[tuple[str, str]] = []
        locations: list[ConfigLocation] = []
        for error in exc.errors():
            key_path = ".".join(str(p) for p in error["loc"])
            error_msg = error["msg"]
            field_errors.append((key_path, error_msg))
            locations.append(
                ConfigLocation(
                    file_path=source_name,
                    key_path=key_path,
                ),
            )
        logger.exception(
            TEMPLATE_RENDER_VALIDATION_ERROR,
            source_name=source_name,
            error_count=len(exc.errors()),
        )
        msg = f"Rendered template failed RootConfig validation: {source_name}"
        raise TemplateValidationError(
            msg,
            locations=tuple(locations),
            field_errors=tuple(field_errors),
        ) from exc
