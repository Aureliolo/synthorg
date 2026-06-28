"""Hierarchical delegation subsystem."""

from synthorg.communication.delegation.authority import (
    AuthorityCheckResult,
    AuthorityValidator,
)
from synthorg.communication.delegation.hierarchy import (
    HierarchyResolver,
)
from synthorg.communication.delegation.service import (
    DelegationService,
)

__all__ = [
    "AuthorityCheckResult",
    "AuthorityValidator",
    "DelegationService",
    "HierarchyResolver",
]
