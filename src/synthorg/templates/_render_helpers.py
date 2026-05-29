"""Internal helpers for the two-pass rendering pipeline.

Department building and RootConfig validation, extracted from the
renderer module.  Should not be imported outside the ``templates``
package.
"""

import copy
from typing import Any

from pydantic import ValidationError

from synthorg.config.errors import ConfigLocation
from synthorg.config.schema import RootConfig
from synthorg.config.utils import to_float
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.template import (
    TEMPLATE_RENDER_TYPE_ERROR,
    TEMPLATE_RENDER_VALIDATION_ERROR,
    TEMPLATE_RENDER_VARIABLE_ERROR,
)
from synthorg.templates.errors import TemplateRenderError, TemplateValidationError

logger = get_logger(__name__)


# ── Department helpers ───────────────────────────────────────


def _parse_budget(dept: dict[str, Any]) -> float:
    """Parse and validate a department's budget_percent value.

    Returns:
        The parsed ``budget_percent`` as a float.

    Raises:
        TemplateRenderError: If the value cannot be converted to float.
    """
    try:
        return to_float(
            dept.get("budget_percent", 0.0),
            field_name=f"departments[{dept.get('name', '')}].budget_percent",
        )
    except ValueError as exc:
        msg = f"Invalid department budget value: {safe_error_description(exc)}"
        logger.warning(
            TEMPLATE_RENDER_TYPE_ERROR,
            department=dept.get("name", ""),
            field="budget_percent",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise TemplateRenderError(msg) from exc


def _resolve_head(
    dept: dict[str, Any],
) -> tuple[str, str | None]:
    """Resolve head_role and optional head_id for a department.

    Returns:
        Tuple of (head_role, head_id or None).
    """
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

    head_merge_id = dept.get("head_merge_id", "")
    head_id: str | None = None
    if head_merge_id and dept.get("head_role"):
        head_id = head_merge_id
    elif head_merge_id:
        logger.warning(
            TEMPLATE_RENDER_VARIABLE_ERROR,
            department=dept_name,
            field="head_merge_id",
            detail=(
                f"head_merge_id {head_merge_id!r} is set but "
                f"head_role is missing; head_merge_id discarded"
            ),
        )
    return head_role, head_id


def _validate_optional_fields(
    dept: dict[str, Any],
    dept_dict: dict[str, Any],
) -> None:
    """Validate and attach optional reporting_lines / policies.

    Raises:
        TemplateRenderError: If types are incorrect.
    """
    dept_name = dept.get("name", "")

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
        dept_dict["reporting_lines"] = copy.deepcopy(reporting_lines)

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
        dept_dict["policies"] = copy.deepcopy(policies)

    ceremony_policy = dept.get("ceremony_policy")
    if ceremony_policy is not None:
        if not isinstance(ceremony_policy, dict):
            msg = f"Department {dept_name!r} 'ceremony_policy' must be a mapping"
            logger.warning(
                TEMPLATE_RENDER_TYPE_ERROR,
                department=dept_name,
                field="ceremony_policy",
                expected="mapping",
                got=type(ceremony_policy).__name__,
            )
            raise TemplateRenderError(msg)
        dept_dict["ceremony_policy"] = ceremony_policy


def _handle_dept_remove(
    dept: dict[str, Any],
    *,
    has_extends: bool,
) -> dict[str, Any]:
    """Validate and return a ``_remove`` marker for a department.

    Returns:
        A single-key ``{"_remove": <name>}`` marker dict for the
        department.

    Raises:
        TemplateRenderError: If ``_remove`` is used without ``extends``.
    """
    dept_name = dept.get("name", "")
    if not has_extends:
        msg = (
            f"Department {dept_name!r} uses '_remove' but the "
            "template has no 'extends' -- directive has no effect"
        )
        logger.error(
            TEMPLATE_RENDER_VARIABLE_ERROR,
            department=dept_name,
            field="_remove",
        )
        raise TemplateRenderError(msg)
    return {"name": dept_name, "_remove": True}


def build_departments(
    raw_depts: list[Any],
    *,
    has_extends: bool = False,
) -> list[dict[str, Any]]:
    """Build RootConfig-compatible department dicts.

    Args:
        raw_depts: List of department dicts from rendered YAML.
        has_extends: Whether the template uses inheritance.

    Returns:
        List of dicts suitable for ``Department`` construction.

    Raises:
        TemplateRenderError: If a department entry is invalid or
            ``_remove`` is used without ``extends``.
    """
    departments: list[dict[str, Any]] = []
    for idx, dept in enumerate(raw_depts):
        if not isinstance(dept, dict):
            msg = f"Department at index {idx} must be a mapping"
            logger.warning(
                TEMPLATE_RENDER_TYPE_ERROR,
                department_index=idx,
                expected="mapping",
                got=type(dept).__name__,
            )
            raise TemplateRenderError(msg)

        if dept.get("_remove"):
            departments.append(
                _handle_dept_remove(dept, has_extends=has_extends),
            )
            continue

        budget_pct = _parse_budget(dept)
        head_role, head_id = _resolve_head(dept)

        dept_dict: dict[str, Any] = {
            "name": dept.get("name", ""),
            "head": head_role,
            "budget_percent": budget_pct,
        }
        if head_id is not None:
            dept_dict["head_id"] = head_id

        _validate_optional_fields(dept, dept_dict)
        departments.append(dept_dict)
    return departments


# ── RootConfig validation ────────────────────────────────────


def validate_as_root_config(
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
        logger.warning(
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
