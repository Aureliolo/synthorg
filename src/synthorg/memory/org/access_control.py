"""Write access control for organizational memory.

Provides seniority-based and human-based write restriction
models, configuration, and enforcement functions.
"""

from types import MappingProxyType
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import (
    OrgFactCategory,
    SeniorityLevel,
    compare_seniority,
)
from synthorg.memory.org.errors import OrgMemoryAccessDeniedError
from synthorg.memory.org.models import OrgFactAuthor  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.org_memory import ORG_MEMORY_WRITE_DENIED

logger = get_logger(__name__)


class CategoryWriteRule(BaseModel):
    """Write permission rule for a single fact category.

    Attributes:
        allowed_seniority: Minimum seniority level for agent writes
            (``None`` means only humans can write).
        human_allowed: Whether human operators can write.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    allowed_seniority: SeniorityLevel | None = Field(
        default=None,
        description=("Minimum seniority level for agent writes (None = human-only)"),
    )
    human_allowed: bool = Field(
        default=True,
        description="Whether human operators can write",
    )


def _default_rules() -> dict[OrgFactCategory, CategoryWriteRule]:
    """Build default write rules for all org fact categories."""
    senior_rule = CategoryWriteRule(
        allowed_seniority=SeniorityLevel.SENIOR,
    )
    return {
        OrgFactCategory.CORE_POLICY: CategoryWriteRule(),
        OrgFactCategory.ADR: senior_rule,
        OrgFactCategory.PROCEDURE: senior_rule,
        OrgFactCategory.CONVENTION: senior_rule,
    }


class WriteAccessConfig(BaseModel):
    """Write access configuration for all fact categories.

    Attributes:
        rules: Per-category write rules (read-only mapping).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    rules: dict[OrgFactCategory, CategoryWriteRule] = Field(
        default_factory=_default_rules,
        description="Per-category write rules",
    )

    @model_validator(mode="after")
    def _wrap_rules_readonly(self) -> Self:
        """Wrap the rules dict in a MappingProxyType for immutability."""
        object.__setattr__(self, "rules", MappingProxyType(dict(self.rules)))
        return self


def check_write_access(
    config: WriteAccessConfig,
    category: OrgFactCategory,
    author: OrgFactAuthor,
) -> bool:
    """Check whether the given author may write to the given category.

    Args:
        config: Write access configuration.
        category: Target fact category.
        author: The author attempting the write.

    Returns:
        ``True`` if write is permitted, ``False`` otherwise.
    """
    # Fail closed: if a category has no explicit rule, deny all writes.
    rule = config.rules.get(
        category,
        CategoryWriteRule(allowed_seniority=None, human_allowed=False),
    )

    if author.is_human:
        return rule.human_allowed

    if rule.allowed_seniority is None:
        return False

    if author.seniority is None:
        return False

    return compare_seniority(author.seniority, rule.allowed_seniority) >= 0


def require_write_access(
    config: WriteAccessConfig,
    category: OrgFactCategory,
    author: OrgFactAuthor,
) -> None:
    """Check write access and raise if denied.

    Args:
        config: Write access configuration.
        category: Target fact category.
        author: The author attempting the write.

    Raises:
        OrgMemoryAccessDeniedError: If write is not permitted.
    """
    if not check_write_access(config, category, author):
        author_desc = (
            "human"
            if author.is_human
            else f"agent {author.agent_id} ({author.seniority})"
        )
        msg = (
            f"Write access denied: {author_desc} cannot write "
            f"to category {category.value!r}"
        )
        logger.warning(
            ORG_MEMORY_WRITE_DENIED,
            category=category.value,
            author_is_human=author.is_human,
            author_agent_id=author.agent_id,
            author_seniority=str(author.seniority) if author.seniority else None,
            reason=msg,
        )
        raise OrgMemoryAccessDeniedError(msg)
