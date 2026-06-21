"""Branch-revert mutator for the rollback executor.

Deletes the remote branch a code-modification apply created: the
inverse of opening a draft PR (the only durable artifact of a code
apply, since local writes are reverted after CI). Backed by the GitHub
API client; deleting the branch also closes its associated draft PR.
"""

import asyncio

from synthorg.meta.errors import RollbackMutationDeniedError
from synthorg.meta.protocol import GitHubAPI
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import META_ROLLBACK_OPERATION_FAILED

logger = get_logger(__name__)


class BranchRevertMutator:
    """Concrete branch-revert mutator backed by the GitHub API client."""

    def __init__(self, *, github_client: GitHubAPI) -> None:
        self._github = github_client

    async def delete_branch(self, *, name: str) -> None:
        """Delete the remote branch ``name`` (closing its draft PR).

        Args:
            name: The branch name (validated non-blank).

        Raises:
            RollbackMutationDeniedError: If ``name`` is blank or the
                underlying GitHub call fails.
            MemoryError: Raised on the corresponding failure path.
            RecursionError: Raised on the corresponding failure path.
            CancelledError: Raised on the corresponding failure path.
        """
        if not name or not name.strip():
            msg = "revert_branch name must be non-blank"
            raise RollbackMutationDeniedError(msg)
        try:
            await self._github.delete_branch(name)
        except MemoryError, RecursionError, asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                META_ROLLBACK_OPERATION_FAILED,
                operation_type="revert_branch",
                target=name,
                reason="branch_delete_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"revert_branch rejected: branch delete failed for {name!r}"
            raise RollbackMutationDeniedError(msg) from exc
