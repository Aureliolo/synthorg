"""Setup template loading and resolution helpers.

Loads and validates a company template by name with API-friendly errors, and
extracts its department data into the shape the company-creation flow
persists. Split out of ``company_helpers`` so each module stays within its
size budget; the locale, password-length, and persistence helpers remain
there.
"""

from typing import NamedTuple

from synthorg.api.controllers.setup_agents import departments_to_json
from synthorg.core.domain_errors import NotFoundError, ValidationError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.setup import (
    SETUP_TEMPLATE_INVALID,
    SETUP_TEMPLATE_NOT_FOUND,
)
from synthorg.templates.loader import LoadedTemplate
from synthorg.templates.schema import CompanyTemplate

logger = get_logger(__name__)


class TemplateResult(NamedTuple):
    """Result of template resolution."""

    departments_json: str
    department_count: int
    template_applied: str | None
    template: CompanyTemplate | None
    loaded: LoadedTemplate | None


def resolve_template(template_name: str | None) -> TemplateResult:
    """Validate template and extract department data.

    Returns:
        ``TemplateResult`` instance. ``loaded`` carries the full
        :class:`LoadedTemplate` so callers can render through the shared
        renderer pipeline (resolving inheritance) rather than the raw template.
    """
    if template_name is None:
        return TemplateResult("", 0, None, None, None)

    loaded = load_template_safe(template_name)
    departments_json = departments_to_json(
        loaded.template.departments,
    )
    return TemplateResult(
        departments_json,
        len(loaded.template.departments),
        template_name,
        loaded.template,
        loaded,
    )


def load_template_safe(template_name: str) -> LoadedTemplate:
    """Load a template by name with API-friendly error handling.

    Args:
        template_name: Template name to load.

    Returns:
        ``LoadedTemplate`` instance.

    Raises:
        NotFoundError: If the template does not exist.
        ValidationError: If it fails to render or validate.
    """
    from synthorg.templates.errors import (  # noqa: PLC0415
        TemplateNotFoundError,
        TemplateRenderError,
        TemplateValidationError,
    )
    from synthorg.templates.loader import (  # noqa: PLC0415
        load_template,
    )

    try:
        return load_template(template_name)
    except TemplateNotFoundError as exc:
        msg = f"Template {template_name!r} not found"
        logger.warning(
            SETUP_TEMPLATE_NOT_FOUND,
            template=template_name,
        )
        raise NotFoundError(msg) from exc
    except (TemplateRenderError, TemplateValidationError) as exc:
        msg = f"Template {template_name!r} is invalid: {safe_error_description(exc)}"
        logger.warning(
            SETUP_TEMPLATE_INVALID,
            template=template_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise ValidationError(msg) from exc
