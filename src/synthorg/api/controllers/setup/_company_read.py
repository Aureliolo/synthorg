# module-kind: code
"""Reconstruct the company config from persisted settings for resume.

The setup wizard is a pure API consumer: it holds no client-side copy of the
company. On resume it hydrates the company from the backend via
``GET /setup/company``, which rebuilds the same :class:`SetupCompanyResponse`
shape that ``POST /setup/company`` returns -- from the ``company.*`` settings
-- so a client that never made the POST (out-of-band creation, cleared
storage, a second browser/device, any non-SPA caller) sees the real company
instead of falling back to a blank "finish with defaults" form.
"""

import json
from typing import Literal

from synthorg.api.controllers.setup.company_helpers import check_has_company
from synthorg.api.controllers.setup_agents import (
    agents_to_summaries,
    get_existing_agents,
)
from synthorg.api.controllers.setup_models import SetupCompanyResponse
from synthorg.core.normalization import normalize_optional_string
from synthorg.observability import get_logger
from synthorg.observability.events.setup import SETUP_STATUS_SETTINGS_UNAVAILABLE
from synthorg.settings.enums import SettingSource
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.service_protocol import SettingsServiceProtocol

logger = get_logger(__name__)


async def _read_db_value(
    settings_svc: SettingsServiceProtocol,
    key: str,
) -> str | None:
    """Read a ``company.<key>`` setting, returning None unless DB-sourced.

    A code-default source means the operator never set it, so it is treated as
    absent (None) rather than leaking a baked-in default into the response.

    Returns:
        The stored string when DB-sourced and non-empty, else ``None``.

    Raises:
        MemoryError: Re-raised; never swallowed.
        RecursionError: Re-raised; never swallowed.
    """
    try:
        entry = await settings_svc.get_entry("company", key)
    except MemoryError, RecursionError:
        raise
    except SettingNotFoundError:
        return None
    except Exception:  # noqa: BLE001 -- settings best-effort: log and skip
        logger.warning(SETUP_STATUS_SETTINGS_UNAVAILABLE, setting=key)
        return None
    if entry.source != SettingSource.DATABASE:
        return None
    return normalize_optional_string(entry.value)


def _department_count(departments_raw: str | None) -> int:
    """Count departments in the persisted JSON blob, tolerant of corruption.

    Returns:
        The number of departments, or 0 when absent / unparseable.
    """
    if not departments_raw:
        return 0
    try:
        parsed = json.loads(departments_raw)
    except json.JSONDecodeError:
        logger.warning(SETUP_STATUS_SETTINGS_UNAVAILABLE, setting="departments")
        return 0
    return len(parsed) if isinstance(parsed, list) else 0


def _parse_budget(raw: str | None) -> float | None:
    """Parse the persisted budget string to a float, or None when unset/invalid.

    Returns:
        The budget as a float, or ``None``.
    """
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning(SETUP_STATUS_SETTINGS_UNAVAILABLE, setting="budget")
        return None


def _normalize_profile(raw: str | None) -> Literal["economy", "balanced", "premium"]:
    """Coerce the persisted tier profile to a known value, defaulting balanced.

    Returns:
        One of the three valid model-tier profiles.
    """
    if raw == "economy":
        return "economy"
    if raw == "premium":
        return "premium"
    return "balanced"


async def build_company_response(
    settings_svc: SettingsServiceProtocol,
) -> SetupCompanyResponse | None:
    """Reconstruct ``SetupCompanyResponse`` from settings, or None if no company.

    Returns:
        The rebuilt company response, or ``None`` when no company exists.
    """
    if not await check_has_company(settings_svc):
        return None
    name = await _read_db_value(settings_svc, "company_name")
    if not name:
        return None
    description = await _read_db_value(settings_svc, "description")
    template_applied = await _read_db_value(settings_svc, "template_applied")
    departments_raw = await _read_db_value(settings_svc, "departments")
    currency = await _read_db_value(settings_svc, "currency")
    profile = await _read_db_value(settings_svc, "model_spend_profile")
    agents = agents_to_summaries(await get_existing_agents(settings_svc))
    return SetupCompanyResponse(
        company_name=name,
        description=description,
        template_applied=template_applied,
        department_count=_department_count(departments_raw),
        currency=currency,
        budget=_parse_budget(await _read_db_value(settings_svc, "budget")),
        model_spend_profile=_normalize_profile(profile),
        agents=agents,
    )


__all__ = ["build_company_response"]
