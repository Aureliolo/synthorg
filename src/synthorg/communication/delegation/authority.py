"""Authority validation for hierarchical delegation."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.communication.config import HierarchyConfig  # noqa: TC001
from synthorg.communication.delegation.hierarchy import (  # noqa: TC001
    HierarchyResolver,
)
from synthorg.core.agent import AgentIdentity  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.delegation import (
    DELEGATION_AUTHORITY_DENIED,
    DELEGATION_AUTHORIZED,
)

logger = get_logger(__name__)


class AuthorityCheckResult(BaseModel):
    """Result of an authority validation check.

    Attributes:
        allowed: Whether the delegation is authorized.
        reason: Explanation (empty on success).
    """

    model_config = ConfigDict(frozen=True)

    allowed: bool = Field(description="Whether delegation is allowed")
    reason: str = Field(default="", description="Explanation")

    @model_validator(mode="after")
    def _validate_allowed_reason(self) -> Self:
        """Enforce allowed/reason correlation."""
        if self.allowed and self.reason:
            msg = "reason must be empty when allowed is True"
            raise ValueError(msg)
        if not self.allowed and not self.reason.strip():
            msg = "reason is required when allowed is False"
            raise ValueError(msg)
        return self


class AuthorityValidator:
    """Validates delegation authority using hierarchy and role permissions.

    Checks:
        1. Hierarchy: delegatee must be a subordinate of delegator
           (direct or skip-level depending on config).
        2. Roles: if ``delegator.authority.can_delegate_to`` is
           non-empty, ``delegatee.role`` must be in it; if empty,
           all roles are permitted.

    Args:
        hierarchy: Resolved org hierarchy.
        hierarchy_config: Hierarchy enforcement configuration.
    """

    __slots__ = ("_config", "_hierarchy")

    def __init__(
        self,
        hierarchy: HierarchyResolver,
        hierarchy_config: HierarchyConfig,
    ) -> None:
        self._hierarchy = hierarchy
        self._config = hierarchy_config

    def validate(
        self,
        delegator: AgentIdentity,
        delegatee: AgentIdentity,
    ) -> AuthorityCheckResult:
        """Validate whether delegator can delegate to delegatee.

        Args:
            delegator: Identity of the delegating agent.
            delegatee: Identity of the target agent.

        Returns:
            Result indicating whether delegation is authorized.
        """
        if self._config.enforce_chain_of_command:
            result = self._check_hierarchy(delegator, delegatee)
            if not result.allowed:
                return result

        result = self._check_role_permissions(delegator, delegatee)
        if not result.allowed:
            return result

        logger.info(
            DELEGATION_AUTHORIZED,
            delegator=delegator.name,
            delegatee=delegatee.name,
        )
        return AuthorityCheckResult(allowed=True)

    def _check_hierarchy(
        self,
        delegator: AgentIdentity,
        delegatee: AgentIdentity,
    ) -> AuthorityCheckResult:
        """Check hierarchy constraints."""
        is_direct = self._hierarchy.is_direct_report(delegator.name, delegatee.name)
        if is_direct:
            return AuthorityCheckResult(allowed=True)

        if self._config.allow_skip_level:
            is_sub = self._hierarchy.is_subordinate(delegator.name, delegatee.name)
            if is_sub:
                return AuthorityCheckResult(allowed=True)

        reason = (
            f"{delegatee.name!r} is not a "
            f"{'subordinate' if self._config.allow_skip_level else 'direct report'} "
            f"of {delegator.name!r}"
        )
        logger.info(
            DELEGATION_AUTHORITY_DENIED,
            delegator=delegator.name,
            delegatee=delegatee.name,
            reason=reason,
        )
        return AuthorityCheckResult(
            allowed=False,
            reason=reason,
        )

    def _check_role_permissions(
        self,
        delegator: AgentIdentity,
        delegatee: AgentIdentity,
    ) -> AuthorityCheckResult:
        """Check role-based delegation permissions."""
        allowed_roles = delegator.authority.can_delegate_to
        if not allowed_roles:
            return AuthorityCheckResult(allowed=True)

        if delegatee.role in allowed_roles:
            return AuthorityCheckResult(allowed=True)

        reason = (
            f"Role {delegatee.role!r} is not in "
            f"delegator's can_delegate_to: {allowed_roles}"
        )
        logger.info(
            DELEGATION_AUTHORITY_DENIED,
            delegator=delegator.name,
            delegatee=delegatee.name,
            reason=reason,
        )
        return AuthorityCheckResult(
            allowed=False,
            reason=reason,
        )
