"""Company-side setup helpers: locale handling, template loading, password length.

Covers company metadata, template resolution, locale storage, and
setup-complete checks. Agent-side helpers (bootstrap, model
selection, tier coverage) live in ``setup._status_checks``,
``setup._runtime_wiring``, and ``setup._embedder_setup``.
"""

import json
from collections.abc import Sequence
from typing import Final, NamedTuple

from synthorg.api.controllers.setup_agents import departments_to_json
from synthorg.core.auth.config import AuthConfig
from synthorg.core.collections import dedupe_preserving_order
from synthorg.core.domain_errors import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.setup import (
    SETUP_ALREADY_COMPLETE,
    SETUP_COMPLETE_CHECK_ERROR,
    SETUP_NAME_LOCALES_CORRUPTED,
    SETUP_NAME_LOCALES_INVALID,
    SETUP_STATUS_SETTINGS_DEFAULT_USED,
    SETUP_STATUS_SETTINGS_UNAVAILABLE,
    SETUP_TEMPLATE_INVALID,
    SETUP_TEMPLATE_NOT_FOUND,
)
from synthorg.settings.enums import SettingSource
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.service import SettingsService
from synthorg.templates.loader import LoadedTemplate
from synthorg.templates.schema import CompanyTemplate

logger = get_logger(__name__)

# Derive from AuthConfig default to prevent silent divergence.
DEFAULT_MIN_PASSWORD_LENGTH: int = AuthConfig.model_fields[
    "min_password_length"
].default

# Truncate persisted-locale corruption logs so a tampered DB blob
# can't blow the structured-log payload while still surfacing enough
# context for triage.
LOCALE_RAW_PREVIEW_LIMIT: Final[int] = 200


async def check_has_company(
    settings_svc: SettingsService,
    *,
    strict: bool = False,
) -> bool:
    """Check whether a company name has been explicitly created.

    Args:
        settings_svc: Settings service instance.
        strict: When True, propagate unexpected exceptions.

    Returns:
        True if a user-created company name exists.

    Raises:
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
        Exception: Raised on the corresponding failure path.
    """
    try:
        entry = await settings_svc.get_entry(
            "company",
            "company_name",
        )
        if entry.source != SettingSource.DATABASE:
            logger.debug(
                SETUP_STATUS_SETTINGS_DEFAULT_USED,
                setting="company_name",
                source=entry.source,
            )
            return False
        return bool(entry.value and entry.value.strip())
    except MemoryError, RecursionError:
        raise
    except SettingNotFoundError:
        logger.debug(
            SETUP_STATUS_SETTINGS_DEFAULT_USED,
            setting="company_name",
        )
        return False
    except Exception:
        logger.warning(
            SETUP_STATUS_SETTINGS_UNAVAILABLE,
            setting="company_name",
        )
        if strict:
            raise
        return False


def validate_locale_selection(
    locales: Sequence[str],
    sentinel: str,
    valid_codes: frozenset[str],
) -> None:
    """Validate locale selection, raising on invalid input.

    Args:
        locales: User-submitted locale codes.
        sentinel: The "all locales" sentinel value.
        valid_codes: Set of valid locale codes.

    Raises:
        ValidationError: On mixed sentinel or invalid codes.
    """
    if sentinel in locales and len(locales) > 1:
        msg = f"'{sentinel}' cannot be combined with explicit locale codes"
        logger.warning(
            SETUP_NAME_LOCALES_INVALID,
            reason="mixed_sentinel",
        )
        raise ValidationError(msg)
    invalid = [loc for loc in locales if loc != sentinel and loc not in valid_codes]
    if invalid:
        logger.warning(
            SETUP_NAME_LOCALES_INVALID,
            invalid_locales=invalid,
        )
        msg = f"Invalid locale codes: {invalid}"
        raise ValidationError(msg)
    unique = list(dedupe_preserving_order(locales))
    if len(unique) != len(locales):
        logger.warning(
            SETUP_NAME_LOCALES_INVALID,
            reason="duplicates",
        )
        msg = "Duplicate locale codes are not allowed"
        raise ValidationError(msg)


async def check_has_name_locales(
    settings_svc: SettingsService,
) -> bool:
    """Check whether name locales have been configured.

    Args:
        settings_svc: Settings service instance.

    Returns:
        True if name locales are user-configured.

    Raises:
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
    """
    try:
        entry = await settings_svc.get_entry(
            "company",
            "name_locales",
        )
    except MemoryError, RecursionError:
        raise
    except SettingNotFoundError:
        return False
    except Exception:  # noqa: BLE001 -- settings best-effort: log and skip
        logger.warning(
            SETUP_STATUS_SETTINGS_UNAVAILABLE,
            setting="name_locales",
        )
        return False
    if entry.source != SettingSource.DATABASE or not entry.value:
        return False
    parsed = parse_locale_json(entry.value)
    return parsed is not None and len(parsed) > 0


async def resolve_min_password_length(
    settings_svc: SettingsService,
) -> int:
    """Resolve the minimum password length from settings.

    Args:
        settings_svc: Settings service instance.

    Returns:
        Resolved minimum password length.

    Raises:
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
    """
    raw_pw_value: str | None = None
    try:
        pw_entry = await settings_svc.get_entry(
            "api",
            "min_password_length",
        )
        raw_pw_value = pw_entry.value
        parsed = int(raw_pw_value)
        return max(parsed, DEFAULT_MIN_PASSWORD_LENGTH)
    except MemoryError, RecursionError:
        raise
    except SettingNotFoundError:
        logger.debug(
            SETUP_STATUS_SETTINGS_DEFAULT_USED,
            setting="min_password_length",
        )
    except ValueError:
        logger.warning(
            SETUP_STATUS_SETTINGS_UNAVAILABLE,
            setting="min_password_length",
            reason="non_integer_value",
            raw=raw_pw_value,
        )
    except Exception:  # noqa: BLE001 -- settings best-effort: log and skip
        logger.warning(
            SETUP_STATUS_SETTINGS_UNAVAILABLE,
            setting="min_password_length",
        )
    return DEFAULT_MIN_PASSWORD_LENGTH


def parse_locale_json(raw: str) -> list[str] | None:
    """Parse and validate a JSON-encoded locale list.

    Returns a ``list`` on success, or ``None`` when invalid.

    Returns:
        The ``list[str]`` value when present, ``None`` otherwise.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError, TypeError:
        logger.warning(
            SETUP_NAME_LOCALES_CORRUPTED,
            reason="invalid_json_or_type",
            raw=(
                raw[:LOCALE_RAW_PREVIEW_LIMIT] if isinstance(raw, str) and raw else None
            ),
        )
        return None
    if not isinstance(parsed, list):
        logger.warning(
            SETUP_NAME_LOCALES_CORRUPTED,
            reason="expected_list",
            actual_type=type(parsed).__name__,
        )
        return None
    if any(not isinstance(locale, str) or not locale.strip() for locale in parsed):
        logger.warning(
            SETUP_NAME_LOCALES_CORRUPTED,
            reason="invalid_locale_items",
        )
        return None
    return parsed


async def read_name_locales(
    settings_svc: SettingsService,
    *,
    resolve: bool = True,
) -> list[str] | None:
    """Read stored name locale preference.

    Args:
        settings_svc: Settings service instance.
        resolve: When True, expand sentinels to concrete codes.

    Returns:
        Locale codes, or None when absent/unparseable.

    Raises:
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
    """
    try:
        entry = await settings_svc.get_entry(
            "company",
            "name_locales",
        )
    except MemoryError, RecursionError:
        raise
    except SettingNotFoundError:
        return None
    except Exception:  # noqa: BLE001 -- settings best-effort: log and skip
        logger.warning(
            SETUP_STATUS_SETTINGS_UNAVAILABLE,
            setting="name_locales",
        )
        return None
    if not entry.value:
        return None
    parsed = parse_locale_json(entry.value)
    if parsed is None:
        return None
    if resolve:
        from synthorg.templates.locales import (  # noqa: PLC0415
            resolve_locales,
        )

        parsed = resolve_locales(parsed)
    return parsed or None


async def is_setup_complete(
    settings_svc: SettingsService,
) -> bool:
    """Check whether setup has been completed.

    Args:
        settings_svc: Settings service instance.

    Returns:
        True if setup_complete is "true".

    Raises:
        Exception: Propagates unexpected errors after logging.
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
    """
    try:
        entry = await settings_svc.get_entry(
            "api",
            "setup_complete",
        )
    except MemoryError, RecursionError:
        raise
    except SettingNotFoundError:
        return False
    except Exception:
        logger.error(
            SETUP_COMPLETE_CHECK_ERROR,
        )
        raise
    else:
        return entry.value == "true"


async def check_setup_not_complete(
    settings_svc: SettingsService,
) -> None:
    """Raise ConflictError if setup has already been completed.

    Raises:
        ConflictError: Raised on the corresponding failure path.
    """
    is_complete = await is_setup_complete(settings_svc)
    if is_complete:
        logger.warning(SETUP_ALREADY_COMPLETE)
        msg = "Setup has already been completed"
        raise ConflictError(msg)


class TemplateResult(NamedTuple):
    """Result of template resolution."""

    departments_json: str
    department_count: int
    template_applied: str | None
    template: CompanyTemplate | None


def resolve_template(template_name: str | None) -> TemplateResult:
    """Validate template and extract department data.

    Returns:
        ``TemplateResult`` instance.
    """
    if template_name is None:
        return TemplateResult("", 0, None, None)

    loaded = load_template_safe(template_name)
    departments_json = departments_to_json(
        loaded.template.departments,
    )
    return TemplateResult(
        departments_json,
        len(loaded.template.departments),
        template_name,
        loaded.template,
    )


async def persist_company_settings(
    settings_svc: SettingsService,
    company_name: str,
    description: str | None,
    departments_json: str,
) -> None:
    """Write description and departments, then company name as the setup marker.

    ``check_has_company`` treats ``company_name`` as the setup-complete
    marker, so write it last; a failure in an earlier ``set`` then
    leaves the instance reading as un-initialised rather than as
    half-initialised.
    """
    await settings_svc.set(
        "company",
        "description",
        description or "",
    )
    await settings_svc.set(
        "company",
        "departments",
        departments_json or "[]",
    )
    await settings_svc.set(
        "company",
        "company_name",
        company_name,
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
