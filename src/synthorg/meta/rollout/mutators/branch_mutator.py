"""Branch-revert mutator for the rollback executor.

Deletes the remote branch a code-modification apply created: the
inverse of opening a draft PR (the only durable artifact of a code
apply, since local writes are reverted after CI). Backed by the GitHub
API client; deleting the branch also closes its associated draft PR.
"""

import asyncio

from synthorg.core.normalization import normalize_base_url
from synthorg.meta.errors import RollbackMutationDeniedError
from synthorg.meta.protocol import GitHubAPI
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import META_ROLLBACK_OPERATION_FAILED

logger = get_logger(__name__)


class BranchRevertMutator:
    """Concrete branch-revert mutator backed by the GitHub API client.

    A ``revert_branch`` operation may only delete a branch the code applier
    itself generated, identified by the configured code-modification branch
    prefix. Constraining deletes to that namespace stops a malformed operation
    from deleting ``main``, a release branch, or any human-owned branch.
    """

    def __init__(self, *, github_client: GitHubAPI, branch_prefix: str) -> None:
        self._github = github_client
        self._branch_namespace = normalize_base_url(branch_prefix)

    async def aclose(self) -> None:
        """Close the GitHub client's lazily-created HTTP connection pool.

        Delegated to by ``RollbackExecutor.aclose`` so the branch-revert
        client (which opens an ``httpx.AsyncClient`` on first use) does not
        leak its pool past the self-improvement service lifecycle.
        """
        close = getattr(self._github, "aclose", None)
        if close is not None:
            await close()

    async def delete_branch(self, *, name: str) -> None:
        """Delete the generated remote branch ``name`` (closing its draft PR).

        Args:
            name: The branch name (validated non-blank and within the
                generated code-modification namespace).

        Raises:
            RollbackMutationDeniedError: If ``name`` is blank, falls outside
                the generated branch namespace, or the underlying GitHub call
                fails.
            MemoryError: Raised on the corresponding failure path.
            RecursionError: Raised on the corresponding failure path.
            CancelledError: Raised on the corresponding failure path.
        """
        branch_name = name.strip() if name else ""
        if not branch_name:
            msg = "revert_branch name must be non-blank"
            raise RollbackMutationDeniedError(msg)
        if not branch_name.startswith(self._branch_namespace):
            msg = "revert_branch name must target a code-modification branch"
            raise RollbackMutationDeniedError(msg)
        try:
            await self._github.delete_branch(branch_name)
        except MemoryError, RecursionError, asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                META_ROLLBACK_OPERATION_FAILED,
                operation_type="revert_branch",
                target=branch_name,
                reason="branch_delete_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"revert_branch rejected: branch delete failed for {branch_name!r}"
            raise RollbackMutationDeniedError(msg) from exc
