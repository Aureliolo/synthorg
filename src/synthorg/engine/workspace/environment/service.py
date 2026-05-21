"""Per-project reproducible-environment provisioning service.

Resolves (and lazily provisions, once) the reproducible environment for a
project from the declaration committed in its workspace.  On first touch
the default declaration is scaffolded (when ``auto_seed``) and committed,
then the configured strategy provisions it.  A persisted row whose
declaration hash and type match the live declaration short-circuits
re-provision; a hash change (declaration edited) or a config kind switch
re-provisions.  Provisioning failure is fail-loud: it is logged and
raised, never silently skipped, so a broken environment never presents
itself as ready.
"""

import asyncio
from pathlib import Path  # noqa: TC003 -- runtime annotation (PEP 649)
from typing import TYPE_CHECKING, Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.project_environment import ProjectEnvironment
from synthorg.engine.workspace.environment.hash_cache import (
    ProvisionedEnvironmentCache,
)
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    ENVIRONMENT_KIND_CHANGED,
    ENVIRONMENT_REUSED,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.engine.workspace.environment.committer import WorkspaceCommitter
    from synthorg.engine.workspace.environment.config import EnvironmentConfig
    from synthorg.engine.workspace.environment.protocol import (
        EnvironmentCommandRunner,
        EnvironmentStrategy,
    )
    from synthorg.persistence.project_environment_protocol import (
        ProjectEnvironmentRepository,
    )

logger = get_logger(__name__)

_COMMIT_MESSAGE: Final[str] = "Declare reproducible environment"


class EnvironmentService:
    """Provisions and resolves the reproducible environment for a project.

    Args:
        repo: Persistence for the :class:`ProjectEnvironment` row.
        strategy: The configured declaration strategy.
        config: Environment config (its ``kind`` is authoritative).
        committer: Commits the declaration into the git-backed workspace
            so a fresh clone receives it.  ``None`` skips committing
            (used in unit tests against a non-git tree).
        cache: In-process provisioned-environment memo.
        clock: Clock seam for row timestamps.
    """

    __slots__ = (
        "_cache",
        "_clock",
        "_committer",
        "_config",
        "_locks",
        "_locks_guard",
        "_repo",
        "_strategy",
    )

    def __init__(  # noqa: PLR0913 -- injected service collaborators, all keyword-only
        self,
        *,
        repo: ProjectEnvironmentRepository,
        strategy: EnvironmentStrategy,
        config: EnvironmentConfig,
        committer: WorkspaceCommitter | None = None,
        cache: ProvisionedEnvironmentCache | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._repo = repo
        self._strategy = strategy
        self._config = config
        self._committer = committer
        self._cache = cache if cache is not None else ProvisionedEnvironmentCache()
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def _lock_for(self, project_id: str) -> asyncio.Lock:
        """Return the per-project provisioning lock (created once)."""
        async with self._locks_guard:
            return self._locks.setdefault(project_id, asyncio.Lock())

    def _matches(self, row: ProjectEnvironment | None, declaration_hash: str) -> bool:
        return (
            row is not None
            and row.environment_type == self._strategy.kind()
            and row.declaration_hash == declaration_hash
        )

    async def get_or_provision(
        self,
        project_id: NotBlankStr,
        *,
        workspace_path: Path,
        runner: EnvironmentCommandRunner,
        sandbox_kind: NotBlankStr,
    ) -> ProjectEnvironment:
        """Return the project's environment, provisioning it once if stale.

        Scaffolds the default declaration (when ``auto_seed``) before
        hashing.  A persisted row matching the live declaration hash and
        type is reused; otherwise the strategy provisions, the
        declaration is committed, and the row is upserted.

        Raises:
            EnvironmentProvisionError: Provisioning failed.
            EnvironmentBackendUnavailableError: The declaration needs a
                backend that is not active.
            EnvironmentConfigError: The declaration is invalid/absent.
        """
        lock = await self._lock_for(project_id)
        async with lock:
            if self._config.auto_seed:
                await self._strategy.scaffold(workspace_path)

            declaration_hash = str(self._strategy.declaration_hash(workspace_path))

            cached = self._cache.get(project_id)
            if self._matches(cached, declaration_hash):
                logger.info(
                    ENVIRONMENT_REUSED,
                    project_id=str(project_id),
                    backend=self._strategy.kind().value,
                    source="memo",
                )
                return cached  # type: ignore[return-value]  # _matches proves not None

            row = await self._repo.get(project_id)
            if self._matches(row, declaration_hash):
                assert row is not None  # noqa: S101 -- _matches guarantees it
                self._cache.set(project_id, row)
                logger.info(
                    ENVIRONMENT_REUSED,
                    project_id=str(project_id),
                    backend=self._strategy.kind().value,
                    source="persisted",
                )
                return row

            if row is not None and row.environment_type != self._strategy.kind():
                logger.warning(
                    ENVIRONMENT_KIND_CHANGED,
                    project_id=str(project_id),
                    from_backend=row.environment_type.value,
                    to_backend=self._strategy.kind().value,
                )

            return await self._provision(
                project_id,
                workspace_path=workspace_path,
                runner=runner,
                sandbox_kind=sandbox_kind,
                prior=row,
            )

    async def _provision(
        self,
        project_id: NotBlankStr,
        *,
        workspace_path: Path,
        runner: EnvironmentCommandRunner,
        sandbox_kind: NotBlankStr,
        prior: ProjectEnvironment | None,
    ) -> ProjectEnvironment:
        """Provision via the strategy, commit, and persist the row."""
        provisioned = await self._strategy.provision(
            project_id=project_id,
            workspace_path=workspace_path,
            runner=runner,
            sandbox_kind=sandbox_kind,
        )
        if self._committer is not None:
            paths = self._strategy.managed_paths(workspace_path)
            await self._committer.commit(
                workspace_path=workspace_path,
                paths=paths,
                message=_COMMIT_MESSAGE,
            )
        now = self._clock.now()
        environment = ProjectEnvironment(
            project_id=project_id,
            environment_type=provisioned.environment_type,
            declaration_hash=provisioned.declaration_hash,
            image_ref=provisioned.image_ref,
            provisioned_at=prior.provisioned_at if prior is not None else now,
            updated_at=now,
        )
        await self._repo.save(environment)
        self._cache.set(project_id, environment)
        return environment


__all__ = ["EnvironmentService"]
