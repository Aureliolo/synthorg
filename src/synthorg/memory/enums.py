"""Memory subsystem enumerations."""

from enum import StrEnum


class ConsolidationInterval(StrEnum):
    """Interval for memory consolidation."""

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    NEVER = "never"


class OrgFactCategory(StrEnum):
    """Category of organizational fact (§7.4).

    Categorizes shared organizational knowledge entries by their nature
    and purpose within the company.
    """

    CORE_POLICY = "core_policy"
    ADR = "adr"
    PROCEDURE = "procedure"
    CONVENTION = "convention"
    ENTITY_DEFINITION = "entity_definition"
