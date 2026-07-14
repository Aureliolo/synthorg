"""Write access control for organizational memory.

Write authority is governed by capability, not rank: whether the
author is a human operator versus an agent (agents are additionally
gated upstream by the ``memory.write`` tool permission their role
grants). The author's role is recorded for provenance.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.memory.enums import OrgFactCategory
from synthorg.memory.org.errors import OrgMemoryAccessDeniedError
from synthorg.memory.org.models import OrgFactAuthor
from synthorg.observability import get_logger
from synthorg.observability.events.org_memory import ORG_MEMORY_WRITE_DENIED

logger = get_logger(__name__)


class CategoryWriteRule(BaseModel):
    """Write permission rule for a single fact category.

    Attributes:
        agent_allowed: Whether agents (already holding the
            ``memory.write`` capability) may write this category.
        human_allowed: Whether human operators can write.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_allowed: bool = Field(
        default=False,
        description="Whether capable agents may write this category",
    )
    human_allowed: bool = Field(
        default=True,
        description="Whether human operators can write",
    )


def _default_rules() -> dict[OrgFactCategory, CategoryWriteRule]:
    """Build default write rules for all org fact categories.

    Core policy stays human-only; the knowledge categories admit
    capable agents (the ``memory.write`` grant is the real gate).

    Returns:
        Mapping from ``OrgFactCategory`` to ``CategoryWriteRule``.
    """
    agent_rule = CategoryWriteRule(agent_allowed=True)
    return {
        OrgFactCategory.CORE_POLICY: CategoryWriteRule(),
        OrgFactCategory.ADR: agent_rule,
        OrgFactCategory.PROCEDURE: agent_rule,
        OrgFactCategory.CONVENTION: agent_rule,
        OrgFactCategory.ENTITY_DEFINITION: agent_rule,
    }


class WriteAccessConfig(BaseModel):
    """Write access configuration for all fact categories.

    The runtime type of ``rules`` is :class:`types.MappingProxyType`,
    expressed in the annotation as :class:`collections.abc.Mapping` so
    callers see an immutable interface at the type boundary instead of
    a freely mutable ``dict``.

    Attributes:
        rules: Per-category write rules (read-only mapping).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    rules: Mapping[OrgFactCategory, CategoryWriteRule] = Field(
        default_factory=_default_rules,
        description="Per-category write rules",
    )

    @model_validator(mode="after")
    def _wrap_rules_readonly(self) -> Self:
        """Wrap the rules mapping in a MappingProxyType for immutability.

        Returns:
            Result of type ``Self``.
        """
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
        CategoryWriteRule(agent_allowed=False, human_allowed=False),
    )

    if author.is_human:
        return rule.human_allowed

    return rule.agent_allowed


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
            "human" if author.is_human else f"agent {author.agent_id} ({author.role})"
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
            author_role=author.role,
            reason=msg,
        )
        raise OrgMemoryAccessDeniedError(msg)
