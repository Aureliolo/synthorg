# module-kind: code
"""Config-dict assembly for the template renderer.

Builds the ``RootConfig``-compatible dict from rendered template data:
shape validation of list fields, numeric extraction, the workflow
sub-dict, and the optional list attachments.
"""

from collections.abc import Mapping
from typing import cast

from pydantic import JsonValue

from synthorg.config.posture_config import PostureConfig
from synthorg.config.utils import deep_merge, to_float
from synthorg.engine.workflow.enums import WorkflowType
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.template import (
    TEMPLATE_RENDER_TYPE_ERROR,
    TEMPLATE_RENDER_YAML_ERROR,
    TEMPLATE_WORKFLOW_CONFIG_UNKNOWN_KEY,
)
from synthorg.templates._agent_expansion import _expand_agents
from synthorg.templates._render_helpers import build_departments
from synthorg.templates.errors import TemplateRenderError
from synthorg.templates.schema import CompanyTemplate

logger = get_logger(__name__)


def thread_posture_knobs(
    result: dict[str, object],
    posture: PostureConfig,
) -> None:
    """Stamp *posture* onto *result* and thread its config-resident knobs.

    Sets ``result["posture"]`` to the resolved flag bundle and threads the
    config-resident knobs (``security.red_team`` / ``budget.auto_downgrade``)
    into any existing section. The settings-resident flags (chat modes,
    steering) are written by the setup-completion seeder, not here. Called
    once at the top of a render with the effective (inheritance + pack-unioned)
    posture.

    The posture knob is the merge base and any template-declared section is
    the override, so an explicit ``security``/``budget`` value in the template
    wins (a template can opt out of a posture default) while other keys in
    those sections survive rather than being clobbered. ``knowledge_substrate``
    has no config knob: it is recorded on ``result["posture"]`` and the
    knowledge engine enables it at boot when a memory backend is present,
    degrading cleanly when one is not.
    """
    result["posture"] = posture.model_dump()
    if posture.red_team:
        result["security"] = _merge_section(
            result.get("security"),
            {
                "red_team": {
                    "enabled": True,
                    "grounding_checker_kind": posture.red_team_grounding,
                },
            },
        )
    if posture.auto_downgrade:
        result["budget"] = _merge_section(
            result.get("budget"),
            {"auto_downgrade": {"enabled": True}},
        )


def _merge_section(
    existing: object,
    posture_knob: dict[str, object],
) -> dict[str, object]:
    """Merge a posture knob under any template-declared section value.

    Returns:
        The template section deep-merged over the posture knob, so explicit
        template values win and other keys survive.
    """
    base = dict(existing) if isinstance(existing, Mapping) else {}
    return deep_merge(posture_knob, base)


def _build_workflow_dict(
    rendered_data: dict[str, object],
    template: CompanyTemplate,
) -> dict[str, object]:
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
    workflow_dict: dict[str, object] = {"workflow_type": workflow_type_str}
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
    rendered_data: dict[str, object],
    template: CompanyTemplate,
    variables: dict[str, object],
    *,
    locales: list[str] | None = None,
    custom_presets: Mapping[str, dict[str, JsonValue]] | None = None,
    preserve_merge_ids: bool = False,
) -> dict[str, object]:
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

    Raises:
        TemplateRenderError: When a rendered field has the wrong shape
            (e.g. ``company`` is not a mapping, or a list field is
            malformed).
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

    result: dict[str, object] = {
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
    rendered_data: dict[str, object],
    result: dict[str, object],
) -> None:
    """Extract optional list fields from rendered data into result."""
    for key in ("workflow_handoffs", "escalation_paths"):
        if key in rendered_data and rendered_data[key] is not None:
            result[key] = _validate_list(rendered_data, key)


def _validate_list(
    rendered_data: dict[str, object],
    key: str,
) -> list[dict[str, object]]:
    """Extract and validate a list field from rendered data.

    Returns:
        The list value for ``key`` (empty list when absent / ``None``),
        with every element confirmed to be a mapping.

    Raises:
        TemplateRenderError: When the field is not a list, or an element
            is not a mapping.
    """
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
    # Every item was just asserted to be a dict above, so narrow the
    # element type from the list's ``object`` members to
    # ``dict[str, object]`` for the return signature without the runtime
    # overhead of re-filtering.
    return cast("list[dict[str, object]]", raw)


def _extract_numeric_config(
    company: dict[str, object],
    template: CompanyTemplate,
) -> tuple[dict[str, object], float]:
    """Extract autonomy and budget_monthly.

    Autonomy is always a dict (AutonomyConfig-compatible). A copy
    is returned to prevent mutation of the original rendered data.

    Returns:
        A ``(autonomy_dict, budget_monthly)`` pair, where ``autonomy_dict``
        is a shallow copy of the rendered autonomy mapping.

    Raises:
        TemplateRenderError: When ``autonomy`` is present but not a
            mapping, or ``budget_monthly`` is not numeric.
    """
    source_name = template.metadata.name
    raw_autonomy = company.get("autonomy", template.autonomy)
    if not isinstance(raw_autonomy, Mapping):
        msg = (
            f"Invalid autonomy config in template {source_name!r}: "
            f"expected mapping, got {type(raw_autonomy).__name__}"
        )
        logger.warning(
            TEMPLATE_RENDER_TYPE_ERROR,
            source=source_name,
            field="autonomy",
            expected="mapping",
            got=type(raw_autonomy).__name__,
        )
        raise TemplateRenderError(msg)
    try:
        # Shallow copy -- autonomy dicts have only scalar values.
        autonomy: dict[str, object] = dict(raw_autonomy)
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
