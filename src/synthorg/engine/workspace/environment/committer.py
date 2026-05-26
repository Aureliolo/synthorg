"""Commits the environment declaration into the git-backed workspace.

After scaffolding and provisioning, the declaration files (and the
generated ``bootstrap.sh``) must be committed so a fresh clone receives
them.  :class:`GitWorkspaceCommitter` stages exactly the strategy's
managed paths and commits them, returning ``False`` when there is
nothing to commit (idempotent re-provision).
"""

from pathlib import Path  # noqa: TC003 -- runtime annotation (PEP 649)
from typing import Final, Protocol, runtime_checkable

from synthorg.engine.errors import EnvironmentProvisionError
from synthorg.engine.workspace._git_subprocess import run_git_subprocess
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import ENVIRONMENT_PROVISION_FAILED

logger = get_logger(__name__)

_DEFAULT_CMD_TIMEOUT_SECONDS: Final[float] = 60.0
_NOTHING_TO_COMMIT: Final[str] = "nothing to commit"


@runtime_checkable
class WorkspaceCommitter(Protocol):
    """Commits a set of workspace-relative paths in the project tree."""

    async def commit(
        self,
        *,
        workspace_path: Path,
        paths: tuple[str, ...],
        message: str,
    ) -> bool:
        """Stage and commit *paths*; return ``True`` if a commit was made."""
        ...


class GitWorkspaceCommitter:
    """Stages and commits declaration paths via ``git``."""

    def __init__(self, *, cmd_timeout: float = _DEFAULT_CMD_TIMEOUT_SECONDS) -> None:
        self._cmd_timeout = cmd_timeout

    async def commit(
        self,
        *,
        workspace_path: Path,
        paths: tuple[str, ...],
        message: str,
    ) -> bool:
        """Stage *paths* and commit them; ``False`` if nothing changed.

        Returns:
            ``True`` when a commit was created; ``False`` when the
            stage left the index clean (nothing to commit).

        Raises:
            EnvironmentProvisionError: When any ``git`` subprocess
                fails outside the "nothing to commit" path.
        """
        if not paths:
            return False
        add_rc, _add_out, _add_err = await run_git_subprocess(
            workspace_path,
            "add",
            "--",
            *paths,
            cmd_timeout=self._cmd_timeout,
            log_event=ENVIRONMENT_PROVISION_FAILED,
        )
        if add_rc != 0:
            logger.warning(
                ENVIRONMENT_PROVISION_FAILED,
                reason="git_add_failed",
                return_code=add_rc,
            )
            msg = f"failed to stage environment declaration (rc={add_rc})"
            raise EnvironmentProvisionError(msg)
        commit_rc, commit_out, commit_err = await run_git_subprocess(
            workspace_path,
            "commit",
            "-m",
            message,
            cmd_timeout=self._cmd_timeout,
            log_event=ENVIRONMENT_PROVISION_FAILED,
        )
        if commit_rc != 0:
            if _NOTHING_TO_COMMIT in f"{commit_out}{commit_err}".lower():
                return False
            logger.warning(
                ENVIRONMENT_PROVISION_FAILED,
                reason="git_commit_failed",
                return_code=commit_rc,
            )
            msg = f"failed to commit environment declaration (rc={commit_rc})"
            raise EnvironmentProvisionError(msg)
        return True


__all__ = ["GitWorkspaceCommitter", "WorkspaceCommitter"]
