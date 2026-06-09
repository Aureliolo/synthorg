"""Template rendering: Jinja2 substitution + validation to RootConfig.

Implements the second pass of the two-pass rendering pipeline:

1. Collect user variables + defaults from the ``CompanyTemplate``.
2. Render the raw YAML text through a Jinja2 ``SandboxedEnvironment``.
3. YAML-parse the rendered text.
4. Build a ``RootConfig``-compatible dict and validate.

Template inheritance (``extends``) is resolved at the renderer level:
each template's Jinja2 is rendered independently, then configs are
merged via :func:`~synthorg.templates.merge.merge_template_configs`.

Config-dict assembly lives in
:mod:`synthorg.templates._config_assembly`; agent expansion lives in
:mod:`synthorg.templates._agent_expansion`.
"""

from collections.abc import Mapping

import yaml
from jinja2 import TemplateError as Jinja2TemplateError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import JsonValue

from synthorg.config.defaults import default_config_dict
from synthorg.config.errors import ConfigLocation
from synthorg.config.schema import RootConfig
from synthorg.config.utils import deep_merge
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.template import (
    TEMPLATE_PACK_CIRCULAR,
    TEMPLATE_PACK_MERGE_START,
    TEMPLATE_PACK_MERGE_SUCCESS,
    TEMPLATE_RENDER_JINJA2_ERROR,
    TEMPLATE_RENDER_START,
    TEMPLATE_RENDER_SUCCESS,
    TEMPLATE_RENDER_VARIABLE_ERROR,
    TEMPLATE_RENDER_YAML_ERROR,
)
from synthorg.templates._config_assembly import _build_config_dict
from synthorg.templates._inheritance import (
    deduplicate_merged_agent_names,
    render_parent_config,
)
from synthorg.templates._render_helpers import validate_as_root_config
from synthorg.templates.errors import TemplateRenderError
from synthorg.templates.loader import LoadedTemplate
from synthorg.templates.merge import merge_template_configs
from synthorg.templates.schema import CompanyTemplate

logger = get_logger(__name__)

# Module-level Jinja2 environment -- stateless and safe to reuse.
_JINJA_ENV = SandboxedEnvironment(keep_trailing_newline=True)
_JINJA_ENV.filters["auto"] = lambda value: value or ""


def render_template(
    loaded: LoadedTemplate,
    variables: dict[str, object] | None = None,
    *,
    locales: list[str] | None = None,
    custom_presets: Mapping[str, dict[str, JsonValue]] | None = None,
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
    variables: dict[str, object] | None = None,
    *,
    locales: list[str] | None = None,
    _chain: frozenset[str] = frozenset(),
    custom_presets: Mapping[str, dict[str, JsonValue]] | None = None,
    _as_parent: bool = False,
) -> dict[str, object]:
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
    base_config: dict[str, object] = {}
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
    base_config: dict[str, object],
    pack_names: tuple[str, ...],
    *,
    variables: dict[str, object] | None = None,
    locales: list[str] | None = None,
    _chain: frozenset[str] = frozenset(),
    custom_presets: Mapping[str, dict[str, JsonValue]] | None = None,
) -> dict[str, object]:
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
    user_vars: dict[str, object],
) -> dict[str, object]:
    """Merge user variables with template defaults.

    Args:
        template: Template with variable declarations.
        user_vars: User-supplied values.

    Returns:
        Complete variable dict.

    Raises:
        TemplateRenderError: If a required variable is missing.
    """
    result: dict[str, object] = {}
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
    variables: dict[str, object],
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
) -> dict[str, object]:
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
