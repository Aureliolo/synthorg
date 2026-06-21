# module-kind: orchestrator
"""On-startup wiring for the meta-loop apply paths.

Builds the three durable stores (active principles, role registry, durable
departments), seeds ``BUILTIN_ROLES`` on first boot, assembles the concrete
:class:`DurablePromptApplierContext` / :class:`DurableArchitectureApplierContext`,
binds the process-global active-principle provider so the prompt build reads
applied principles, and constructs :class:`SelfImprovementService` on
``MetaStateSlice`` so ``apply()`` is reachable in a real deployment (the
``PENDING SelfImprovementService`` ghost-wiring line flips to ``ENFORCED``).

Best-effort + idempotent: an already-wired service short-circuits, and a
missing dependency (no persistence) leaves the meta-loop unwired rather than
poisoning startup. The durable A/B-test write path comes live with this hook
because ``SelfImprovementService`` is the consumer of ``ab_test_repo``.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.pagination import collect_all
from synthorg.core.role_catalog import BUILTIN_ROLES
from synthorg.core.role_record import RoleRecord
from synthorg.core.types import NotBlankStr
from synthorg.engine.strategy.active_principle import ActivePrinciple
from synthorg.engine.strategy.active_principle_provider import (
    ActivePrincipleLoader,
    CachedActivePrincipleProvider,
    set_active_principle_provider,
)
from synthorg.engine.strategy.principle_override_provider import (
    CachedPrincipleOverrideProvider,
    PrincipleOverrideLoader,
    set_principle_override_provider,
)
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.rollout.rollback import RollbackExecutor
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.observability.events.role import ROLE_REGISTRY_SEEDED
from synthorg.organization.services import DepartmentService
from synthorg.persistence.active_principle_protocol import ActivePrincipleRepository
from synthorg.persistence.department_protocol import DepartmentRepository
from synthorg.persistence.principle_override_protocol import (
    PrincipleOverrideRepository,
)
from synthorg.persistence.role_registry_protocol import RoleRegistryRepository

logger = get_logger(__name__)


async def wire_meta_apply(app_state: AppState) -> None:
    """Wire the durable meta-loop apply paths at boot.

    Args:
        app_state: The application state to wire onto.
    """
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415

    if app_state.slice(MetaStateSlice).self_improvement_service is not None:
        return
    if app_state.slice(PersistenceStateSlice).backend is None:
        logger.info(
            API_APP_STARTUP,
            service="self_improvement",
            note="persistence absent; meta-loop apply paths unwired",
        )
        return
    try:
        await _wire(app_state)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="self_improvement",
            note="meta-loop apply wiring failed; appliers stay unwired",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire(app_state: AppState) -> None:
    from synthorg.approval.state import approval_store_of  # noqa: PLC0415
    from synthorg.engine.state import workflow_service_of  # noqa: PLC0415
    from synthorg.hr.state import agent_registry_of  # noqa: PLC0415
    from synthorg.meta.appliers._contexts import (  # noqa: PLC0415
        DurableArchitectureApplierContext,
        DurablePromptApplierContext,
    )
    from synthorg.meta.service import SelfImprovementService  # noqa: PLC0415
    from synthorg.meta.state import (  # noqa: PLC0415
        MetaStateSlice,
        self_improvement_config_of,
    )
    from synthorg.settings.state import (  # noqa: PLC0415
        config_resolver_of,
        settings_service_of,
    )

    principle_repo = _build_active_principle_repo(app_state)
    role_repo = _build_role_registry_repo(app_state)
    department_repo = _build_department_repo(app_state)

    await _seed_builtin_roles(role_repo, app_state)

    provider = CachedActivePrincipleProvider(loader=_principle_loader(principle_repo))
    await provider.refresh()
    set_active_principle_provider(provider)

    override_repo = _build_principle_override_repo(app_state)
    override_provider = CachedPrincipleOverrideProvider(
        loader=_principle_override_loader(override_repo)
    )
    await override_provider.refresh()
    set_principle_override_provider(override_provider)

    department_service = await _wire_department_service(app_state, department_repo)
    architecture_context = DurableArchitectureApplierContext(
        role_repo=role_repo,
        department_service=department_service,
        workflow_service=workflow_service_of(app_state),
        agent_registry=agent_registry_of(app_state),
        clock=app_state.clock,
    )
    await architecture_context.refresh_snapshot()

    prompt_context = DurablePromptApplierContext(
        principle_repo=principle_repo,
        provider=provider,
        # Bind the architecture context's live snapshot accessors so roles /
        # departments created by an architecture apply (which refreshes that
        # context) are immediately visible to prompt dry-run scope validation
        # without a process restart.
        role_names=architecture_context.role_names,
        department_names=architecture_context.department_names,
        clock=app_state.clock,
    )

    config = await self_improvement_config_of(app_state)
    rollback_executor = _build_rollback_executor(
        app_state=app_state,
        config=config,
        active_principle_repo=principle_repo,
        active_principle_provider=provider,
        override_repo=override_repo,
        override_provider=override_provider,
        role_repo=role_repo,
        department_service=department_service,
    )
    service = SelfImprovementService(
        config=config,
        prompt_context=prompt_context,
        architecture_context=architecture_context,
        settings_writer=settings_service_of(app_state),
        approval_store=approval_store_of(app_state),
        config_resolver=config_resolver_of(app_state),
        ab_test_record_sink=app_state.slice(MetaStateSlice).ab_test_repo,
        clock=app_state.clock,
        rollback_executor=rollback_executor,
    )
    app_state.wire(MetaStateSlice, self_improvement_service=service)
    logger.info(
        API_APP_STARTUP, service="self_improvement", note="wired (durable apply)"
    )


def _build_rollback_executor(  # noqa: PLR0913
    *,
    app_state: AppState,
    config: SelfImprovementConfig,
    active_principle_repo: ActivePrincipleRepository,
    active_principle_provider: CachedActivePrincipleProvider,
    override_repo: PrincipleOverrideRepository,
    override_provider: CachedPrincipleOverrideProvider,
    role_repo: RoleRegistryRepository,
    department_service: DepartmentService,
) -> RollbackExecutor:
    """Assemble the rollback executor from the durable stores at boot.

    The config / prompt-restore / principle-removal / architecture / code
    mutators wire unconditionally. The branch mutator (and thus the
    ``revert_branch`` handler) wires only when code-modification GitHub
    credentials are configured, so a deployment without them still gets an
    executor servicing every other operation and failing loudly on
    ``revert_branch`` rather than silently dropping it.

    Returns:
        The assembled :class:`RollbackExecutor`.
    """
    from synthorg.engine.state import workflow_service_of  # noqa: PLC0415
    from synthorg.engine.workspace.state import (  # noqa: PLC0415
        agent_workspace_root_of,
    )
    from synthorg.meta.appliers.github_client import HttpGitHubClient  # noqa: PLC0415
    from synthorg.meta.factory import build_rollback_executor  # noqa: PLC0415
    from synthorg.meta.rollout.mutators import (  # noqa: PLC0415
        ActivePrincipleRemovalMutator,
        BranchRevertMutator,
        PrincipleOverridePromptMutator,
        RoutedArchitectureMutator,
        SettingsServiceConfigMutator,
        WorkspaceCodeMutator,
        build_architecture_adapters,
    )
    from synthorg.settings.state import settings_service_of  # noqa: PLC0415

    config_mutator = SettingsServiceConfigMutator(
        settings_service=settings_service_of(app_state),
    )
    prompt_mutator = PrincipleOverridePromptMutator(
        override_repo=override_repo,
        on_override_written=override_provider.refresh,
    )
    principle_removal_mutator = ActivePrincipleRemovalMutator(
        repo=active_principle_repo,
        on_principle_removed=active_principle_provider.refresh,
    )
    architecture_mutator = RoutedArchitectureMutator(
        build_architecture_adapters(
            role_repo=role_repo,
            department_service=department_service,
            workflow_service=workflow_service_of(app_state),
            clock=app_state.clock,
        ),
    )
    code_mutator = WorkspaceCodeMutator(
        workspace_root=agent_workspace_root_of(app_state),
    )
    # The branch mutator (and thus the ``revert_branch`` handler) wires only
    # when code-modification GitHub credentials are configured.
    code_cfg = config.code_modification
    branch_mutator = (
        BranchRevertMutator(
            github_client=HttpGitHubClient(
                token=str(code_cfg.github_token),
                repo=str(code_cfg.github_repo),
                api_base_url=str(code_cfg.github_api_url),
                base_branch=str(code_cfg.base_branch),
                timeout=code_cfg.api_timeout_seconds,
            ),
            branch_prefix=str(code_cfg.branch_prefix),
        )
        if code_cfg.github_token is not None and code_cfg.github_repo is not None
        else None
    )
    return build_rollback_executor(
        config_mutator=config_mutator,
        prompt_mutator=prompt_mutator,
        architecture_mutator=architecture_mutator,
        code_mutator=code_mutator,
        principle_removal_mutator=principle_removal_mutator,
        branch_mutator=branch_mutator,
    )


def _principle_loader(repo: ActivePrincipleRepository) -> ActivePrincipleLoader:
    async def _load() -> tuple[ActivePrinciple, ...]:
        return await collect_all(
            lambda limit, offset: repo.list_items(limit=limit, offset=offset)
        )

    return _load


def _build_principle_override_repo(
    app_state: AppState,
) -> PrincipleOverrideRepository:
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    return persistence_of(app_state).principle_overrides


def _principle_override_loader(
    repo: PrincipleOverrideRepository,
) -> PrincipleOverrideLoader:
    async def _load() -> dict[str, str]:
        rows = await collect_all(
            lambda limit, offset: repo.list_items(limit=limit, offset=offset)
        )
        return {row.scope: row.text for row in rows}

    return _load


async def _seed_builtin_roles(
    role_repo: RoleRegistryRepository, app_state: AppState
) -> None:
    """Seed any absent built-in roles into the durable registry (idempotent)."""
    now = app_state.clock.now()
    seeded = 0
    for role in BUILTIN_ROLES:
        if await role_repo.get(NotBlankStr(role.name)) is None:
            await role_repo.save(
                RoleRecord(role=role, is_builtin=True, created_at=now, updated_at=now)
            )
            seeded += 1
    if seeded:
        logger.info(ROLE_REGISTRY_SEEDED, count=seeded)


def _build_active_principle_repo(app_state: AppState) -> ActivePrincipleRepository:
    from synthorg.persistence.backend_dispatch import build_for_backend  # noqa: PLC0415
    from synthorg.persistence.db_handle import (  # noqa: PLC0415
        postgres_pool,
        sqlite_connection,
    )
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    persistence = persistence_of(app_state)

    def _sqlite() -> ActivePrincipleRepository:
        from synthorg.persistence.sqlite.active_principle_repo import (  # noqa: PLC0415
            SQLiteActivePrincipleRepository,
        )

        return SQLiteActivePrincipleRepository(
            sqlite_connection(persistence), write_context=persistence.write_context
        )

    def _postgres() -> ActivePrincipleRepository:
        from synthorg.persistence.postgres.active_principle_repo import (  # noqa: PLC0415
            PostgresActivePrincipleRepository,
        )

        return PostgresActivePrincipleRepository(postgres_pool(persistence))

    return build_for_backend(persistence, sqlite=_sqlite, postgres=_postgres)


def _build_role_registry_repo(app_state: AppState) -> RoleRegistryRepository:
    from synthorg.persistence.backend_dispatch import build_for_backend  # noqa: PLC0415
    from synthorg.persistence.db_handle import (  # noqa: PLC0415
        postgres_pool,
        sqlite_connection,
    )
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    persistence = persistence_of(app_state)

    def _sqlite() -> RoleRegistryRepository:
        from synthorg.persistence.sqlite.role_registry_repo import (  # noqa: PLC0415
            SQLiteRoleRegistryRepository,
        )

        return SQLiteRoleRegistryRepository(
            sqlite_connection(persistence), write_context=persistence.write_context
        )

    def _postgres() -> RoleRegistryRepository:
        from synthorg.persistence.postgres.role_registry_repo import (  # noqa: PLC0415
            PostgresRoleRegistryRepository,
        )

        return PostgresRoleRegistryRepository(postgres_pool(persistence))

    return build_for_backend(persistence, sqlite=_sqlite, postgres=_postgres)


def _build_department_repo(app_state: AppState) -> DepartmentRepository:
    from synthorg.persistence.backend_dispatch import build_for_backend  # noqa: PLC0415
    from synthorg.persistence.db_handle import (  # noqa: PLC0415
        postgres_pool,
        sqlite_connection,
    )
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    persistence = persistence_of(app_state)

    def _sqlite() -> DepartmentRepository:
        from synthorg.persistence.sqlite.department_repo import (  # noqa: PLC0415
            SQLiteDepartmentRepository,
        )

        return SQLiteDepartmentRepository(
            sqlite_connection(persistence), write_context=persistence.write_context
        )

    def _postgres() -> DepartmentRepository:
        from synthorg.persistence.postgres.department_repo import (  # noqa: PLC0415
            PostgresDepartmentRepository,
        )

        return PostgresDepartmentRepository(postgres_pool(persistence))

    return build_for_backend(persistence, sqlite=_sqlite, postgres=_postgres)


async def _wire_department_service(
    app_state: AppState, repo: DepartmentRepository
) -> DepartmentService:
    """Build a durable, rehydrated department service and publish it.

    Wires the repo-backed :class:`DepartmentService` onto
    ``OrganizationStateSlice`` so controllers and the architecture applier
    share one durable instance whose cache survives restart.

    Returns:
        The durable department service.
    """
    from synthorg.organization.state import OrganizationStateSlice  # noqa: PLC0415

    service = DepartmentService(repo=repo, clock=app_state.clock)
    await service.rehydrate()
    app_state.wire(OrganizationStateSlice, department_service=service)
    return service


__all__ = ["wire_meta_apply"]
