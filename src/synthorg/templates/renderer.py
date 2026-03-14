"""Template rendering: Jinja2 substitution + validation to RootConfig.

Implements the second pass of the two-pass rendering pipeline:

1. Collect user variables + defaults from the ``CompanyTemplate``.
2. Render the raw YAML text through a Jinja2 ``SandboxedEnvironment``.
3. YAML-parse the rendered text.
4. Build a ``RootConfig``-compatible dict and validate.

Template inheritance (``extends``) is resolved at the renderer level:
each template's Jinja2 is rendered independently, then configs are
merged via :func:`~synthorg.templates.merge.merge_template_configs`.
"""

from typing import TYPE_CHECKING, Any

import yaml
from jinja2 import TemplateError as Jinja2TemplateError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import ValidationError

from synthorg.config.defaults import default_config_dict
from synthorg.config.errors import ConfigLocation
from synthorg.config.schema import RootConfig
from synthorg.config.utils import deep_merge, to_float
from synthorg.core.agent import PersonalityConfig
from synthorg.observability import get_logger
from synthorg.observability.events.template import (
    TEMPLATE_INHERIT_CIRCULAR,
    TEMPLATE_INHERIT_DEPTH_EXCEEDED,
    TEMPLATE_INHERIT_RESOLVE_START,
    TEMPLATE_INHERIT_RESOLVE_SUCCESS,
    TEMPLATE_PERSONALITY_PRESET_UNKNOWN,
    TEMPLATE_RENDER_JINJA2_ERROR,
    TEMPLATE_RENDER_START,
    TEMPLATE_RENDER_SUCCESS,
    TEMPLATE_RENDER_TYPE_ERROR,
    TEMPLATE_RENDER_VALIDATION_ERROR,
    TEMPLATE_RENDER_VARIABLE_ERROR,
    TEMPLATE_RENDER_YAML_ERROR,
)
from synthorg.templates.errors import (
    TemplateInheritanceError,
    TemplateRenderError,
    TemplateValidationError,
)
from synthorg.templates.merge import DEFAULT_MERGE_DEPARTMENT, merge_template_configs
from synthorg.templates.presets import (
    generate_auto_name,
    get_personality_preset,
)

# Placeholder provider name resolved by the engine at startup.
_DEFAULT_PROVIDER = "default"

# Default department when not specified in template agent config.
_DEFAULT_DEPARTMENT = DEFAULT_MERGE_DEPARTMENT

# Maximum inheritance chain depth.
_MAX_INHERITANCE_DEPTH = 10

if TYPE_CHECKING:
    from synthorg.templates.loader import LoadedTemplate
    from synthorg.templates.schema import CompanyTemplate

logger = get_logger(__name__)

# Module-level Jinja2 environment — stateless and safe to reuse.
_JINJA_ENV = SandboxedEnvironment(keep_trailing_newline=True)
_JINJA_ENV.filters["auto"] = lambda value: value or ""


def render_template(
    loaded: LoadedTemplate,
    variables: dict[str, Any] | None = None,
) -> RootConfig:
    """Render a loaded template into a validated RootConfig.

    Resolves template inheritance (``extends``) before validation.

    Args:
        loaded: :class:`LoadedTemplate` from the loader.
        variables: User-supplied variable values (overrides defaults).

    Returns:
        Validated, frozen :class:`RootConfig`.

    Raises:
        TemplateRenderError: If rendering fails.
        TemplateValidationError: If validation fails.
        TemplateInheritanceError: If inheritance resolution fails.
    """
    logger.info(
        TEMPLATE_RENDER_START,
        source_name=loaded.source_name,
    )
    config_dict = _render_to_dict(loaded, variables)

    # Merge with defaults and validate.
    merged = deep_merge(default_config_dict(), config_dict)
    result = _validate_as_root_config(merged, loaded.source_name)
    logger.info(
        TEMPLATE_RENDER_SUCCESS,
        source_name=loaded.source_name,
    )
    return result


def _render_to_dict(
    loaded: LoadedTemplate,
    variables: dict[str, Any] | None = None,
    *,
    _chain: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Render a template to a config dict, resolving inheritance.

    Args:
        loaded: Loaded template.
        variables: User-supplied variables.
        _chain: Set of already-seen template identifiers for circular
            detection (internal use).

    Returns:
        Config dict suitable for merging with defaults.
    """
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

    # Build config dict from the rendered data.
    child_config = _build_config_dict(rendered_data, template, vars_dict)

    # If no inheritance, return child config directly.
    if template.extends is None:
        return child_config

    # Resolve inheritance chain.
    return _resolve_inheritance(
        child_config=child_config,
        loaded=loaded,
        vars_dict=vars_dict,
        _chain=_chain,
    )


def _resolve_inheritance(
    *,
    child_config: dict[str, Any],
    loaded: LoadedTemplate,
    vars_dict: dict[str, Any],
    _chain: frozenset[str],
) -> dict[str, Any]:
    """Resolve template inheritance for a child config.

    Loads and renders the parent, detects circular dependencies and
    depth violations, then merges parent + child.

    Args:
        child_config: Already-rendered child config dict.
        loaded: The child's :class:`LoadedTemplate`.
        vars_dict: Child's resolved variables.
        _chain: Already-visited parent names for circular detection.

    Returns:
        Merged config dict (parent + child).

    Raises:
        TemplateInheritanceError: On circular chains or depth overflow.
    """
    # Guaranteed by _render_to_dict caller.
    assert loaded.template.extends is not None  # noqa: S101
    parent_name: str = loaded.template.extends
    child_id = loaded.source_name

    logger.info(
        TEMPLATE_INHERIT_RESOLVE_START,
        child=child_id,
        parent=parent_name,
    )

    _validate_inheritance_chain(child_id, parent_name, _chain)

    merged = _render_and_merge_parent(
        parent_name,
        child_config,
        vars_dict,
        _chain,
    )
    logger.info(
        TEMPLATE_INHERIT_RESOLVE_SUCCESS,
        child=child_id,
        parent=parent_name,
    )
    return merged


def _validate_inheritance_chain(
    child_id: str,
    parent_name: str,
    _chain: frozenset[str],
) -> None:
    """Check for circular inheritance and depth overflow."""
    if parent_name in _chain:
        logger.error(
            TEMPLATE_INHERIT_CIRCULAR,
            child=child_id,
            parent=parent_name,
            chain=sorted(_chain),
        )
        msg = (
            f"Circular template inheritance: {child_id!r} extends "
            f"{parent_name!r}, which is already in the inheritance chain"
        )
        raise TemplateInheritanceError(msg)

    if len(_chain) >= _MAX_INHERITANCE_DEPTH:
        logger.error(
            TEMPLATE_INHERIT_DEPTH_EXCEEDED,
            child=child_id,
            depth=len(_chain),
            max_depth=_MAX_INHERITANCE_DEPTH,
        )
        msg = (
            f"Template inheritance depth exceeded ({len(_chain)} >= "
            f"{_MAX_INHERITANCE_DEPTH}): {child_id!r}"
        )
        raise TemplateInheritanceError(msg)


def _render_and_merge_parent(
    parent_name: str,
    child_config: dict[str, Any],
    vars_dict: dict[str, Any],
    _chain: frozenset[str],
) -> dict[str, Any]:
    """Load, render, and merge a parent template with a child config."""
    from synthorg.templates.loader import load_template  # noqa: PLC0415

    parent_loaded = load_template(parent_name)
    parent_vars = _collect_parent_variables(
        parent_loaded.template,
        vars_dict,
    )
    parent_config = _render_to_dict(
        parent_loaded,
        parent_vars,
        _chain=_chain | {parent_name},
    )
    return merge_template_configs(parent_config, child_config)


def _collect_parent_variables(
    parent_template: CompanyTemplate,
    child_vars: dict[str, Any],
) -> dict[str, Any]:
    """Collect variables for a parent template.

    Child's resolved variables serve as defaults for the parent.
    Parent's own defaults fill gaps.

    Args:
        parent_template: The parent template.
        child_vars: Child's resolved variables.

    Returns:
        Variable dict for parent rendering.
    """
    result: dict[str, Any] = dict(child_vars)
    for var in parent_template.variables:
        if var.name not in result and var.default is not None:
            result[var.name] = var.default
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
    try:
        jinja_template = _JINJA_ENV.from_string(raw_yaml)
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
        logger.error(TEMPLATE_RENDER_YAML_ERROR, source_name=source_name, error=msg)
        raise TemplateRenderError(
            msg,
            locations=(ConfigLocation(file_path=source_name),),
        )

    template_data = data["template"]
    if not isinstance(template_data, dict):
        msg = f"Rendered template 'template' key must be a mapping: {source_name}"
        logger.error(TEMPLATE_RENDER_YAML_ERROR, source_name=source_name, error=msg)
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
    company = rendered_data.get("company")
    if company is None:
        company = {}
    elif not isinstance(company, dict):
        msg = "Rendered template 'company' must be a mapping"
        logger.error(TEMPLATE_RENDER_YAML_ERROR, error=msg)
        raise TemplateRenderError(msg)

    company_name = variables.get(
        "company_name",
        template.metadata.name,
    )

    has_extends = template.extends is not None
    agents = _expand_agents(
        _validate_list(rendered_data, "agents"),
        has_extends=has_extends,
    )
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

    _attach_optional_lists(rendered_data, result)

    return result


def _attach_optional_lists(
    rendered_data: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """Extract optional list fields from rendered data into result."""
    for key in ("workflow_handoffs", "escalation_paths"):
        if key in rendered_data and rendered_data[key] is not None:
            result[key] = _validate_list(rendered_data, key)


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
        logger.warning(
            TEMPLATE_RENDER_TYPE_ERROR,
            field=key,
            expected="list",
            got=type(raw).__name__,
        )
        raise TemplateRenderError(msg)
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            msg = (
                f"Rendered template {key!r}[{i}] must be a "
                f"mapping, got {type(item).__name__}"
            )
            logger.warning(
                TEMPLATE_RENDER_TYPE_ERROR,
                field=f"{key}[{i}]",
                expected="mapping",
                got=type(item).__name__,
            )
            raise TemplateRenderError(msg)
    return raw


def _extract_numeric_config(
    company: dict[str, Any],
    template: CompanyTemplate,
) -> tuple[float | dict[str, Any], float]:
    """Extract autonomy and budget_monthly.

    Autonomy may be a float (backward compat) or a dict. When it's
    a float, we pass it through — the ``CompanyConfig.model_validator``
    converts it to ``AutonomyConfig``.
    """
    source_name = template.metadata.name
    raw_autonomy = company.get("autonomy", template.autonomy)
    try:
        if isinstance(raw_autonomy, dict):
            # Already an AutonomyConfig-like dict — deep-copy to prevent
            # mutation of the original rendered data.
            autonomy: float | dict[str, Any] = dict(raw_autonomy)
        else:
            autonomy = to_float(raw_autonomy, field_name="autonomy")
        budget_monthly = to_float(
            company.get("budget_monthly", template.budget_monthly),
            field_name="budget_monthly",
        )
    except ValueError as exc:
        msg = f"Invalid numeric value in rendered template {source_name!r}: {exc}"
        logger.warning(
            TEMPLATE_RENDER_TYPE_ERROR,
            source=source_name,
            error=str(exc),
        )
        raise TemplateRenderError(msg) from exc
    return autonomy, budget_monthly


def _expand_agents(
    raw_agents: list[dict[str, Any]],
    *,
    has_extends: bool,
) -> list[dict[str, Any]]:
    """Expand template agent dicts into AgentConfig-compatible dicts.

    Args:
        raw_agents: List of agent dicts from rendered YAML.
        has_extends: Whether the template uses inheritance.

    Returns:
        List of dicts suitable for ``AgentConfig`` construction.
    """
    used_names: set[str] = set()
    expanded: list[dict[str, Any]] = []
    for idx, agent in enumerate(raw_agents):
        expanded.append(
            _expand_single_agent(
                agent,
                idx,
                used_names,
                has_extends=has_extends,
            ),
        )
    return expanded


def _expand_single_agent(
    agent: dict[str, Any],
    idx: int,
    used_names: set[str],
    *,
    has_extends: bool,
) -> dict[str, Any]:
    """Expand a single template agent dict.

    Steps: auto-name generation, name deduplication, personality
    preset/inline resolution, and model tier assignment.
    """
    role = agent.get("role")
    if not role:
        msg = f"Agent at index {idx} is missing required 'role' field"
        logger.warning(TEMPLATE_RENDER_VARIABLE_ERROR, index=idx, field="role")
        raise TemplateRenderError(msg)
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

    personality = _resolve_agent_personality(agent, name)
    if personality is not None:
        agent_dict["personality"] = personality

    model_tier = agent.get("model", "medium")
    agent_dict["model"] = {"provider": _DEFAULT_PROVIDER, "model_id": model_tier}

    # Preserve _remove merge directive for inheritance.
    if agent.get("_remove"):
        if not has_extends:
            msg = (
                f"Agent {name!r} uses '_remove' but the template "
                "has no 'extends' — directive has no effect"
            )
            logger.warning(
                TEMPLATE_RENDER_VARIABLE_ERROR,
                agent=name,
                field="_remove",
            )
            raise TemplateRenderError(msg)
        agent_dict["_remove"] = True

    # Preserve merge_id only when inheritance is active — standalone
    # templates have no merge step to strip it later.
    merge_id = str(agent.get("merge_id", "")).strip()
    if has_extends and merge_id:
        agent_dict["merge_id"] = merge_id

    return agent_dict


def _resolve_agent_personality(
    agent: dict[str, Any],
    name: str,
) -> dict[str, Any] | None:
    """Resolve personality from inline config or named preset.

    Args:
        agent: Raw agent dict from rendered YAML.
        name: Resolved agent name for error context.

    Returns:
        Personality dict, or ``None`` if no personality configured.

    Raises:
        TemplateRenderError: If personality config is invalid or preset
            is unknown.
    """
    inline_personality = agent.get("personality")
    preset_name = agent.get("personality_preset")
    if inline_personality is not None:
        if not isinstance(inline_personality, dict):
            msg = (
                f"Personality for agent {name!r} must be a mapping, "
                f"got {type(inline_personality).__name__}"
            )
            logger.warning(
                TEMPLATE_RENDER_TYPE_ERROR,
                agent=name,
                field="personality",
                got=type(inline_personality).__name__,
            )
            raise TemplateRenderError(msg)
        _validate_inline_personality(inline_personality, name)
        return dict(inline_personality)
    if preset_name:
        try:
            return get_personality_preset(preset_name)
        except KeyError as exc:
            msg = f"Unknown personality preset {preset_name!r} for agent {name!r}"
            logger.warning(
                TEMPLATE_PERSONALITY_PRESET_UNKNOWN,
                agent=name,
                preset=preset_name,
            )
            raise TemplateRenderError(msg) from exc
    return None


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
    except (ValidationError, TypeError) as exc:
        logger.warning(
            TEMPLATE_RENDER_VALIDATION_ERROR,
            agent_name=agent_name,
            error=str(exc),
        )
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
            logger.warning(
                TEMPLATE_RENDER_TYPE_ERROR,
                department=dept.get("name", ""),
                field="budget_percent",
                error=str(exc),
            )
            raise TemplateRenderError(msg) from exc
        dept_name = dept.get("name", "")
        head_role = dept.get("head_role")
        if not head_role:
            logger.warning(
                TEMPLATE_RENDER_VARIABLE_ERROR,
                department=dept_name,
                field="head_role",
                detail="No head_role specified; using department name as placeholder",
            )
            head_role = dept_name or ""
        dept_dict: dict[str, Any] = {
            "name": dept_name,
            "head": head_role,
            "budget_percent": budget_pct,
        }
        reporting_lines = dept.get("reporting_lines")
        if reporting_lines is not None:
            if not isinstance(reporting_lines, list):
                msg = f"Department {dept_name!r} 'reporting_lines' must be a list"
                logger.warning(
                    TEMPLATE_RENDER_TYPE_ERROR,
                    department=dept_name,
                    field="reporting_lines",
                    expected="list",
                    got=type(reporting_lines).__name__,
                )
                raise TemplateRenderError(msg)
            dept_dict["reporting_lines"] = reporting_lines
        policies = dept.get("policies")
        if policies is not None:
            if not isinstance(policies, dict):
                msg = f"Department {dept_name!r} 'policies' must be a mapping"
                logger.warning(
                    TEMPLATE_RENDER_TYPE_ERROR,
                    department=dept_name,
                    field="policies",
                    expected="mapping",
                    got=type(policies).__name__,
                )
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
