"""Infrastructure-domain MCP args.

Covers health, settings, providers, backup, audit, events, users,
projects, requests, setup, simulations, template_packs,
integration_health.
"""

from pydantic import Field

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type
from synthorg.meta.mcp.domains._common_args import (
    AdminGuardrailFields,
    IsoDatetimeStr,
    PaginationFields,
    _ArgsBase,
)


class HealthCheckArgs(_ArgsBase):
    """Args for ``health.check``: no fields."""


class SettingsListArgs(PaginationFields):
    """Args for ``settings.list``."""


class SettingsGetArgs(_ArgsBase):
    """Args for ``settings.get``."""

    key: NotBlankStr = Field(description="Setting key")


class SettingsUpdateArgs(AdminGuardrailFields):
    """Args for ``settings.update`` (admin op)."""

    key: NotBlankStr = Field(description="Setting key")
    value: str = Field(description="New value")


class SettingsDeleteArgs(AdminGuardrailFields):
    """Args for ``settings.delete``.

    Destructive admin op: callers must supply ``confirm=True`` and a
    non-blank ``reason`` (mixin), in addition to the setting key.
    """

    key: NotBlankStr = Field(description="Setting key")


class ProvidersListArgs(_ArgsBase):
    """Args for ``providers.list``: no fields."""


class _ProviderNameArgs(_ArgsBase):
    """Mixin for tools keyed by ``provider_name``."""

    provider_name: NotBlankStr = Field(description="Provider name")


class ProvidersGetArgs(_ProviderNameArgs):
    """Args for ``providers.get``."""


class ProvidersGetHealthArgs(_ProviderNameArgs):
    """Args for ``providers.get_health``."""


class ProvidersTestConnectionArgs(_ProviderNameArgs, AdminGuardrailFields):
    """Args for ``providers.test_connection`` (admin op)."""


class BackupCreateArgs(AdminGuardrailFields):
    """Args for ``backup.create`` (admin op)."""

    trigger: NotBlankStr = Field(description="What initiated the backup")


class BackupListArgs(_ArgsBase):
    """Args for ``backup.list``: no fields."""


class _BackupIdArgs(_ArgsBase):
    """Mixin for tools keyed by ``backup_id``."""

    backup_id: NotBlankStr = Field(description="Backup UUID")


class BackupGetArgs(_BackupIdArgs):
    """Args for ``backup.get``."""


class BackupDeleteArgs(_BackupIdArgs, AdminGuardrailFields):
    """Args for ``backup.delete``.

    Destructive admin op: callers must supply ``confirm=True`` and a
    non-blank ``reason`` (mixin), in addition to the backup UUID.
    """


class BackupRestoreArgs(_BackupIdArgs, AdminGuardrailFields):
    """Args for ``backup.restore``.

    Restoring overwrites the current system state.  Treat as
    destructive: callers must supply ``confirm=True`` and a non-blank
    ``reason`` (mixin) in addition to the backup UUID.
    """


class AuditListArgs(PaginationFields):
    """Args for ``audit.list``."""

    agent_id: NotBlankStr | None = Field(default=None, description="Filter by agent")
    tool_name: NotBlankStr | None = Field(default=None, description="Filter by tool")
    action_type: NotBlankStr | None = Field(
        default=None,
        description="Filter by action type",
    )
    verdict: NotBlankStr | None = Field(default=None, description="Filter by verdict")
    since: IsoDatetimeStr | None = Field(
        default=None, description="Start datetime (ISO 8601, timezone-aware)"
    )
    until: IsoDatetimeStr | None = Field(
        default=None, description="End datetime (ISO 8601, timezone-aware)"
    )


class EventsListArgs(PaginationFields):
    """Args for ``events.list``."""

    event_type: NotBlankStr | None = Field(
        default=None,
        description="Filter by event type",
    )


class UsersListArgs(PaginationFields):
    """Args for ``users.list``."""


class _UserIdArgs(_ArgsBase):
    """Mixin for tools keyed by ``user_id``."""

    user_id: NotBlankStr = Field(description="User UUID")


class UsersGetArgs(_UserIdArgs):
    """Args for ``users.get``."""


class UsersCreateArgs(AdminGuardrailFields):
    """Args for ``users.create`` (admin op)."""

    username: NotBlankStr = Field(description="Username")
    role: NotBlankStr = Field(description="User role")


class UsersUpdateFields(_ArgsBase):
    """Mutable fields exposed on the ``users.update`` patch DTO.

    Every field is optional so partial patches can land; the
    ``extra="forbid"`` config inherited from ``_ArgsBase`` rejects
    unknown keys at the boundary instead of letting them through to a
    later validation stage.
    """

    role: NotBlankStr | None = Field(
        default=None,
        description="New access-control role for the user",
    )
    must_change_password: bool | None = Field(
        default=None,
        description="Force the user to change password on next login",
    )


class UsersUpdateArgs(_UserIdArgs, AdminGuardrailFields):
    """Args for ``users.update`` (admin op)."""

    updates: UsersUpdateFields = Field(description="Fields to update")


class UsersDeleteArgs(_UserIdArgs, AdminGuardrailFields):
    """Args for ``users.delete``.

    Destructive admin op: callers must supply ``confirm=True`` and a
    non-blank ``reason`` (mixin), in addition to the user UUID.
    """


class ProjectsListArgs(PaginationFields):
    """Args for ``projects.list``."""


class _ProjectIdArgs(_ArgsBase):
    """Mixin for tools keyed by ``project_id``."""

    project_id: NotBlankStr = Field(description="Project UUID")


class ProjectsGetArgs(_ProjectIdArgs):
    """Args for ``projects.get``."""


class ProjectsCreateArgs(_ArgsBase):
    """Args for ``projects.create``."""

    name: NotBlankStr = Field(description="Project name")
    description: str = Field(default="", description="Project description")


class ProjectsUpdateArgs(_ProjectIdArgs):
    """Args for ``projects.update``."""

    updates: dict[str, object] = Field(description="Fields to update")


class ProjectsDeleteArgs(_ProjectIdArgs, AdminGuardrailFields):
    """Args for ``projects.delete``.

    Destructive admin op: callers must supply ``confirm=True`` and a
    non-blank ``reason`` (mixin), in addition to the project UUID.
    """


class RequestsListArgs(PaginationFields):
    """Args for ``requests.list``."""


class RequestsGetArgs(_ArgsBase):
    """Args for ``requests.get``."""

    request_id: NotBlankStr = Field(description="Request UUID")


class RequestsCreateArgs(_ArgsBase):
    """Args for ``requests.create``."""

    type: NotBlankStr = Field(description="Request type")
    content: str = Field(description="Request content")


class SetupGetStatusArgs(_ArgsBase):
    """Args for ``setup.get_status``: no fields."""


class SetupInitializeArgs(AdminGuardrailFields):
    """Args for ``setup.initialize`` (admin op)."""

    config: dict[str, object] = Field(description="Initial configuration")


class SimulationsListArgs(PaginationFields):
    """Args for ``simulations.list``."""


class SimulationsGetArgs(_ArgsBase):
    """Args for ``simulations.get``."""

    simulation_id: NotBlankStr = Field(description="Simulation UUID")


class SimulationsCreateArgs(_ArgsBase):
    """Args for ``simulations.create``."""

    scenario: NotBlankStr = Field(description="Simulation scenario")
    parameters: dict[str, object] = Field(
        default_factory=dict,
        description="Simulation parameters",
    )


class TemplatePacksListArgs(PaginationFields):
    """Args for ``template_packs.list``."""


class _PackIdArgs(_ArgsBase):
    """Mixin for tools keyed by ``pack_id``."""

    pack_id: NotBlankStr = Field(description="Template pack UUID")


class TemplatePacksGetArgs(_PackIdArgs):
    """Args for ``template_packs.get``."""


class TemplatePacksInstallArgs(AdminGuardrailFields):
    """Args for ``template_packs.install`` (admin op).

    A new install is keyed by ``name`` + ``version`` (the pack's natural
    composite key); the persisted ``pack_id`` UUID only exists after a
    successful install, so ``uninstall`` is the path that takes a
    ``pack_id``.
    """

    name: NotBlankStr = Field(description="Template pack name")
    version: NotBlankStr = Field(description="Template pack version")


class TemplatePacksUninstallArgs(_PackIdArgs, AdminGuardrailFields):
    """Args for ``template_packs.uninstall`` (admin op)."""


class IntegrationHealthGetAllArgs(_ArgsBase):
    """Args for ``integration_health.get_all``: no fields."""


class IntegrationHealthGetArgs(_ArgsBase):
    """Args for ``integration_health.get``."""

    integration_name: NotBlankStr = Field(description="Integration name")
