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
from synthorg.config.utils import deep_merge, to_float
from synthorg.core.enums import WorkflowType
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.template import (
    TEMPLATE_PACK_CIRCULAR,
    TEMPLATE_PACK_MERGE_START,
    TEMPLATE_PACK_MERGE_SUCCESS,
    TEMPLATE_RENDER_JINJA2_ERROR,
    TEMPLATE_RENDER_START,
    TEMPLATE_RENDER_SUCCESS,
    TEMPLATE_RENDER_TYPE_ERROR,
    TEMPLATE_RENDER_VARIABLE_ERROR,
    TEMPLATE_RENDER_YAML_ERROR,
    TEMPLATE_WORKFLOW_CONFIG_UNKNOWN_KEY,
)
from synthorg.templates._inheritance import (
    deduplicate_merged_agent_names,
    render_parent_config,
)
from synthorg.templates._preset_resolution import resolve_agent_personality
from synthorg.templates._render_helpers import (
    build_departments,
    validate_as_root_config,
)
from synthorg.templates.errors import TemplateRenderError
from synthorg.templates.merge import DEFAULT_MERGE_DEPARTMENT, merge_template_configs
from synthorg.templates.presets import generate_auto_name

# Placeholder provider name resolved by the engine at startup.
_DEFAULT_PROVIDER = "default"

# Default department when not specified in template agent config.
_DEFAULT_DEPARTMENT = DEFAULT_MERGE_DEPARTMENT

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.config.schema import RootConfig
    from synthorg.templates.loader import LoadedTemplate
    from synthorg.templates.schema import CompanyTemplate

logger = get_logger(__name__)

# Module-level Jinja2 environment -- stateless and safe to reuse.
_JINJA_ENV = SandboxedEnvironment(keep_trailing_newline=True)
_JINJA_ENV.filters["auto"] = lambda value: value or ""


def render_template(
    loaded: LoadedTemplate,
    variables: dict[str, Any] | None = None,
    *,
    locales: list[str] | None = None,
    custom_presets: Mapping[str, dict[str, Any]] | None = None,
) -> RootConfig:
    """Render a loaded template into a validated RootConfig.

    Resolves template inheritance (``extends``) before validation.

    Args:
        loaded: :class:`LoadedTemplate` from the loader.
        variables: User-supplied variable values (overrides defaults).
        locales: Faker locale codes for auto-name generation.
            Defaults to all Latin-script locales when ``None``.
        custom_presets: Optional mapping of custom preset names to
            personality config dicts for resolving user-defined presets.

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
    config_dict = _render_to_dict(
        loaded,
        variables,
        locales=locales,
        custom_presets=custom_presets,
    )

    # Merge with defaults and validate.
    merged = deep_merge(default_config_dict(), config_dict)
    result = validate_as_root_config(merged, loaded.source_name)
    logger.info(
        TEMPLATE_RENDER_SUCCESS,
        source_name=loaded.source_name,
    )
    return result


def _render_to_dict(
    loaded: LoadedTemplate,
    variables: dict[str, Any] | None = None,
    *,
    locales: list[str] | None = None,
    _chain: frozenset[str] = frozenset(),
    custom_presets: Mapping[str, dict[str, Any]] | None = None,
    _as_parent: bool = False,
) -> dict[str, Any]:
    """Render a template to a config dict, resolving inheritance.

    Args:
        loaded: Loaded template.
        variables: User-supplied variables.
        locales: Faker locale codes for auto-name generation.
        _chain: Set of already-seen template identifiers for circular
            detection (internal use).
        custom_presets: Optional custom preset mapping.
        _as_parent: When ``True``, preserve ``merge_id`` on agents
            even if this template has no ``extends``.  Used when
            rendering a parent template whose agents may be targeted
            by a child's ``merge_id``-based overrides.

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
    child_config = _build_config_dict(
        rendered_data,
        template,
        vars_dict,
        locales=locales,
        custom_presets=custom_presets,
        preserve_merge_ids=_as_parent,
    )

    # Build base config from extends parent (if any).
    base_config: dict[str, Any] = {}
    if template.extends is not None:
        base_config = render_parent_config(
            parent_name=template.extends,
            child_id=loaded.source_name,
            vars_dict=vars_dict,
            _chain=_chain,
            locales=locales,
            custom_presets=custom_presets,
            render_to_dict_fn=_render_to_dict,
        )

    # Layer packs onto base (after extends, before child).
    if template.uses_packs:
        base_config = _resolve_packs(
            base_config,
            template.uses_packs,
            variables=vars_dict,
            locales=locales,
            _chain=_chain,
            custom_presets=custom_presets,
        )

    # Merge child on top of base (child wins).
    if base_config:
        result = merge_template_configs(base_config, child_config)
        return deduplicate_merged_agent_names(result)

    return child_config


def _resolve_packs(
    base_config: dict[str, Any],
    pack_names: tuple[str, ...],
    *,
    variables: dict[str, Any] | None = None,
    locales: list[str] | None = None,
    _chain: frozenset[str] = frozenset(),
    custom_presets: Mapping[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Merge template packs onto a base config in declaration order.

    Each pack is loaded, rendered to a config dict, and merged onto
    the accumulated base.  The caller merges the child on top
    afterward, so the child always wins.

    Args:
        base_config: Accumulated config (from extends parent, or
            empty dict for standalone templates).
        pack_names: Pack names in declaration order.
        variables: Caller/template variables to thread into pack
            rendering so parameterized packs resolve correctly.
        locales: Faker locale codes for auto-name generation.
        _chain: Already-seen template identifiers.
        custom_presets: Optional custom preset mapping.

    Returns:
        Merged config dict with all packs applied.

    Raises:
        TemplateRenderError: If a pack is not found, fails to render,
            or a circular pack dependency is detected.
    """
    from synthorg.templates.pack_loader import load_pack  # noqa: PLC0415

    result = base_config
    for pack_name in pack_names:
        if pack_name in _chain:
            logger.error(
                TEMPLATE_PACK_CIRCULAR,
                pack_name=pack_name,
                chain=sorted(_chain),
            )
            msg = (
                f"Circular pack dependency: {pack_name!r} is already "
                f"in the resolution chain {sorted(_chain)}"
            )
            raise TemplateRenderError(msg)
        logger.info(
            TEMPLATE_PACK_MERGE_START,
            pack_name=pack_name,
        )
        pack_loaded = load_pack(pack_name)
        pack_config = _render_to_dict(
            pack_loaded,
            variables,
            locales=locales,
            _chain=_chain | {pack_name},
            custom_presets=custom_presets,
            _as_parent=True,
        )
        result = merge_template_configs(result, pack_config)
        logger.info(
            TEMPLATE_PACK_MERGE_SUCCESS,
            pack_name=pack_name,
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
        logger.warning(
            TEMPLATE_RENDER_JINJA2_ERROR,
            source_name=source_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = (
            f"Jinja2 rendering failed for {source_name}: {safe_error_description(exc)}"
        )
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
        logger.warning(
            TEMPLATE_RENDER_YAML_ERROR,
            source_name=source_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Rendered template YAML is invalid for {source_name}: {safe_error_description(exc)}"  # noqa: E501
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


def _build_workflow_dict(
    rendered_data: dict[str, Any],
    template: CompanyTemplate,
) -> dict[str, Any]:
    """Build a WorkflowConfig-compatible dict from workflow type and sub-configs.

    Args:
        rendered_data: Parsed dict from the rendered YAML.
        template: Original template metadata (for fallback workflow type).

    Returns:
        Dict suitable for the ``workflow`` key on ``RootConfig``.
    """
    workflow_type_raw = rendered_data.get("workflow", template.workflow.value)
    workflow_type_str = (
        workflow_type_raw.value
        if isinstance(workflow_type_raw, WorkflowType)
        else str(workflow_type_raw)
    )
    workflow_dict: dict[str, Any] = {"workflow_type": workflow_type_str}
    wf_config = rendered_data.get("workflow_config")
    if isinstance(wf_config, dict):
        known_keys = {"kanban", "sprint"}
        for key in known_keys:
            if key in wf_config:
                workflow_dict[key] = wf_config[key]
        unknown = set(wf_config) - known_keys
        if unknown:
            logger.warning(
                TEMPLATE_WORKFLOW_CONFIG_UNKNOWN_KEY,
                unknown_keys=sorted(unknown),
                source_name=template.metadata.name,
            )
    return workflow_dict


def _build_config_dict(  # noqa: PLR0913
    rendered_data: dict[str, Any],
    template: CompanyTemplate,
    variables: dict[str, Any],
    *,
    locales: list[str] | None = None,
    custom_presets: Mapping[str, dict[str, Any]] | None = None,
    preserve_merge_ids: bool = False,
) -> dict[str, Any]:
    """Build a RootConfig-compatible dict from rendered template data.

    Args:
        rendered_data: Parsed dict from the rendered YAML.
        template: Original template metadata (for fallback values).
        variables: Collected variables.
        locales: Faker locale codes for auto-name generation.
        custom_presets: Optional custom preset mapping.
        preserve_merge_ids: Force ``merge_id`` preservation even when
            the template itself has no ``extends``.  Used for parent
            rendering.

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
    preserve_merge = has_extends or preserve_merge_ids
    agents = _expand_agents(
        _validate_list(rendered_data, "agents"),
        has_extends=has_extends,
        locales=locales,
        custom_presets=custom_presets,
        preserve_merge_ids=preserve_merge,
    )
    departments = build_departments(
        _validate_list(rendered_data, "departments"),
        has_extends=has_extends,
    )

    autonomy, budget_monthly = _extract_numeric_config(company, template)

    result: dict[str, Any] = {
        "company_name": company_name,
        "company_type": company.get("type", template.metadata.company_type.value),
        "agents": agents,
        "departments": departments,
        "workflow": _build_workflow_dict(rendered_data, template),
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
) -> tuple[dict[str, Any], float]:
    """Extract autonomy and budget_monthly.

    Autonomy is always a dict (AutonomyConfig-compatible). A copy
    is returned to prevent mutation of the original rendered data.
    """
    source_name = template.metadata.name
    raw_autonomy = company.get("autonomy", template.autonomy)
    if not isinstance(raw_autonomy, dict):
        msg = (
            f"Invalid autonomy config in template {source_name!r}: "
            f"expected dict, got {type(raw_autonomy).__name__}"
        )
        logger.warning(
            TEMPLATE_RENDER_TYPE_ERROR,
            source=source_name,
            field="autonomy",
            expected="dict",
            got=type(raw_autonomy).__name__,
        )
        raise TemplateRenderError(msg)
    try:
        # Shallow copy -- autonomy dicts have only scalar values.
        autonomy: dict[str, Any] = dict(raw_autonomy)
        budget_monthly = to_float(
            company.get("budget_monthly", template.budget_monthly),
            field_name="budget_monthly",
        )
    except ValueError as exc:
        msg = f"Invalid numeric value in rendered template {source_name!r}: {safe_error_description(exc)}"  # noqa: E501
        logger.warning(
            TEMPLATE_RENDER_TYPE_ERROR,
            source=source_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise TemplateRenderError(msg) from exc
    return autonomy, budget_monthly


def _expand_agents(
    raw_agents: list[dict[str, Any]],
    *,
    has_extends: bool,
    locales: list[str] | None = None,
    custom_presets: Mapping[str, dict[str, Any]] | None = None,
    preserve_merge_ids: bool = False,
) -> list[dict[str, Any]]:
    """Expand template agent dicts into AgentConfig-compatible dicts.

    Args:
        raw_agents: List of agent dicts from rendered YAML.
        has_extends: Whether the template uses inheritance.
        locales: Faker locale codes for auto-name generation.
        custom_presets: Optional custom preset mapping.
        preserve_merge_ids: Preserve ``merge_id`` on expanded agents.

    Returns:
        List of dicts suitable for ``AgentConfig`` construction.
    """
    keep_merge = preserve_merge_ids or has_extends
    used_names: set[str] = set()
    expanded: list[dict[str, Any]] = []
    for idx, agent in enumerate(raw_agents):
        expanded.append(
            _expand_single_agent(
                agent,
                idx,
                used_names,
                has_extends=has_extends,
                locales=locales,
                custom_presets=custom_presets,
                preserve_merge_id=keep_merge,
            ),
        )
    return expanded


def _expand_single_agent(  # noqa: PLR0913
    agent: dict[str, Any],
    idx: int,
    used_names: set[str],
    *,
    has_extends: bool,
    locales: list[str] | None = None,
    custom_presets: Mapping[str, dict[str, Any]] | None = None,
    preserve_merge_id: bool = False,
) -> dict[str, Any]:
    """Expand a single template agent dict.

    Steps: auto-name generation, name deduplication, personality
    preset/inline resolution, model tier assignment, and merge
    directive handling.

    Args:
        agent: Raw agent dict from rendered YAML.
        idx: Zero-based index for error context.
        used_names: Set of already-used names for deduplication.
        has_extends: Whether the template uses inheritance.
        locales: Faker locale codes for auto-name generation.
        custom_presets: Optional custom preset mapping for resolving
            user-defined presets.
        preserve_merge_id: Preserve ``merge_id`` on the expanded agent.

    Returns:
        Expanded agent dict suitable for ``AgentConfig`` construction.
    """
    role = agent.get("role")
    if not role:
        msg = f"Agent at index {idx} is missing required 'role' field"
        logger.warning(TEMPLATE_RENDER_VARIABLE_ERROR, index=idx, field="role")
        raise TemplateRenderError(msg)
    name = str(agent.get("name") or "").strip()

    if not name or name.startswith("{{") or "__JINJA2__" in name:
        name = generate_auto_name(role, seed=idx, locales=locales)

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

    personality = resolve_agent_personality(
        agent,
        name,
        custom_presets=custom_presets,
    )
    if personality is not None:
        agent_dict["personality"] = personality

    model_tier = _resolve_model_tier(agent)
    agent_dict["model"] = {"provider": _DEFAULT_PROVIDER, "model_id": model_tier}

    # Preserve _remove merge directive for inheritance.
    if agent.get("_remove"):
        if not has_extends:
            msg = (
                f"Agent {name!r} uses '_remove' but the template "
                "has no 'extends' -- directive has no effect"
            )
            logger.warning(
                TEMPLATE_RENDER_VARIABLE_ERROR,
                agent=name,
                field="_remove",
            )
            raise TemplateRenderError(msg)
        agent_dict["_remove"] = True

    # Preserve merge_id when inheritance is active or when rendering
    # as a parent (so child templates can target agents by merge_id).
    keep_merge = preserve_merge_id or has_extends
    merge_id_raw = agent.get("merge_id") or ""
    merge_id = str(merge_id_raw).strip()
    if keep_merge and merge_id:
        agent_dict["merge_id"] = merge_id

    return agent_dict


def _resolve_model_tier(agent: dict[str, Any]) -> str:
    """Extract the model tier from a template agent dict.

    Handles both the string format (``"medium"``) and the structured
    ``ModelRequirement`` dict format
    (``{"tier": "medium", "priority": "quality"}``).

    The renderer path sets a placeholder ``model_id``; structured
    requirements are only fully threaded through the setup wizard path
    which calls ``match_all_agents``.

    Args:
        agent: Raw template agent dict from Jinja2 rendering.

    Returns:
        Tier string (``"large"``, ``"medium"``, or ``"small"``).

    Raises:
        TemplateRenderError: If a dict model contains invalid fields.
    """
    model_raw = agent.get("model", "medium")
    if isinstance(model_raw, dict):
        from synthorg.templates.model_requirements import (  # noqa: PLC0415
            parse_model_requirement,
        )

        try:
            return parse_model_requirement(model_raw).tier
        except (ValidationError, ValueError) as exc:
            msg = f"Invalid structured model requirement: {safe_error_description(exc)}"
            logger.warning(
                TEMPLATE_RENDER_TYPE_ERROR,
                field="model",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise TemplateRenderError(msg) from exc
    return str(model_raw)
