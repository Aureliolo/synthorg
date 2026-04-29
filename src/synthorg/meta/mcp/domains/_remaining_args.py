"""Typed argument models for the remaining MCP domains.

Covers ``communication`` / ``integrations`` / ``infrastructure`` /
``memory`` (the largest set, ~95 tools combined).  Per-domain section
headers below.
"""

from typing import Literal

from pydantic import Field

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type
from synthorg.meta.mcp.domains._common_args import (
    DestructiveGuardrailFields,
    PaginationFields,
    _ArgsBase,
)

# ─────────────────────────────────────────────────────────────────────
# Communication: messages, meetings, connections, webhooks, tunnel
# ─────────────────────────────────────────────────────────────────────


class MessagesListArgs(PaginationFields):
    """Args for ``messages.list``."""

    channel: NotBlankStr | None = Field(default=None, description="Filter by channel")
    sender: NotBlankStr | None = Field(default=None, description="Filter by sender")


class MessagesGetArgs(_ArgsBase):
    """Args for ``messages.get``."""

    message_id: NotBlankStr = Field(description="Message UUID")


class MessagesSendArgs(_ArgsBase):
    """Args for ``messages.send``."""

    channel: NotBlankStr = Field(description="Target channel")
    content: NotBlankStr = Field(description="Message content")
    sender: NotBlankStr | None = Field(default=None, description="Sender name")


class MessagesDeleteArgs(DestructiveGuardrailFields):
    """Args for ``messages.delete`` (destructive)."""

    message_id: NotBlankStr = Field(description="Message UUID")


class MeetingsListArgs(PaginationFields):
    """Args for ``meetings.list``."""


class MeetingsGetArgs(_ArgsBase):
    """Args for ``meetings.get``."""

    meeting_id: NotBlankStr = Field(description="Meeting UUID")


class MeetingsCreateArgs(_ArgsBase):
    """Args for ``meetings.create``."""

    title: NotBlankStr = Field(description="Meeting title")
    participants: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Participant names",
    )


class MeetingsUpdateArgs(_ArgsBase):
    """Args for ``meetings.update``."""

    meeting_id: NotBlankStr = Field(description="Meeting UUID")
    updates: dict[str, object] = Field(description="Fields to update")


class MeetingsDeleteArgs(DestructiveGuardrailFields):
    """Args for ``meetings.delete`` (destructive)."""

    meeting_id: NotBlankStr = Field(description="Meeting UUID")


class ConnectionsListArgs(_ArgsBase):
    """Args for ``connections.list``: no fields."""


class ConnectionsGetArgs(_ArgsBase):
    """Args for ``connections.get``."""

    name: NotBlankStr = Field(description="Connection name")


class ConnectionsCreateArgs(_ArgsBase):
    """Args for ``connections.create``."""

    name: NotBlankStr = Field(description="Connection name")
    connection_type: NotBlankStr = Field(description="Connection type")
    credentials: dict[str, object] = Field(
        default_factory=dict,
        description="Connection credentials",
    )


class ConnectionsDeleteArgs(_ArgsBase):
    """Args for ``connections.delete``."""

    name: NotBlankStr = Field(description="Connection name")


class ConnectionsCheckHealthArgs(_ArgsBase):
    """Args for ``connections.check_health``."""

    name: NotBlankStr = Field(description="Connection name")


class WebhooksListArgs(PaginationFields):
    """Args for ``webhooks.list``."""


class WebhooksGetArgs(_ArgsBase):
    """Args for ``webhooks.get``."""

    webhook_id: NotBlankStr = Field(description="Webhook UUID")


class WebhooksCreateArgs(_ArgsBase):
    """Args for ``webhooks.create``."""

    url: NotBlankStr = Field(description="Webhook URL")
    events: tuple[NotBlankStr, ...] = Field(
        min_length=1,
        description="Event types to subscribe",
    )


class WebhooksUpdateArgs(_ArgsBase):
    """Args for ``webhooks.update``."""

    webhook_id: NotBlankStr = Field(description="Webhook UUID")
    updates: dict[str, object] = Field(description="Fields to update")


class WebhooksDeleteArgs(_ArgsBase):
    """Args for ``webhooks.delete``."""

    webhook_id: NotBlankStr = Field(description="Webhook UUID")


class TunnelGetStatusArgs(_ArgsBase):
    """Args for ``tunnel.get_status``: no fields."""


class TunnelConnectArgs(_ArgsBase):
    """Args for ``tunnel.connect``."""

    target: NotBlankStr = Field(description="Tunnel target endpoint")


# ─────────────────────────────────────────────────────────────────────
# Integrations: mcp_catalog, oauth, clients, artifacts, ontology
# ─────────────────────────────────────────────────────────────────────


class McpCatalogListArgs(PaginationFields):
    """Args for ``mcp_catalog.list``."""


class McpCatalogSearchArgs(_ArgsBase):
    """Args for ``mcp_catalog.search``."""

    query: NotBlankStr = Field(description="Search query")


class McpCatalogGetArgs(_ArgsBase):
    """Args for ``mcp_catalog.get``."""

    catalog_id: NotBlankStr = Field(description="Catalog entry ID")


class McpCatalogInstallArgs(_ArgsBase):
    """Args for ``mcp_catalog.install``."""

    catalog_id: NotBlankStr = Field(description="Catalog entry to install")


class McpCatalogUninstallArgs(_ArgsBase):
    """Args for ``mcp_catalog.uninstall``."""

    install_id: NotBlankStr = Field(description="Installation ID")


class OauthListProvidersArgs(_ArgsBase):
    """Args for ``oauth.list_providers``: no fields."""


class OauthConfigureProviderArgs(_ArgsBase):
    """Args for ``oauth.configure_provider``."""

    provider: NotBlankStr = Field(description="Provider name")
    config: dict[str, object] = Field(description="OAuth configuration")


class OauthRemoveProviderArgs(_ArgsBase):
    """Args for ``oauth.remove_provider``."""

    provider: NotBlankStr = Field(description="Provider name")


class ClientsListArgs(PaginationFields):
    """Args for ``clients.list``."""


class _ClientIdArgs(_ArgsBase):
    """Mixin for tools keyed by ``client_id``."""

    client_id: NotBlankStr = Field(description="Client UUID")


class ClientsGetArgs(_ClientIdArgs):
    """Args for ``clients.get``."""


class ClientsCreateArgs(_ArgsBase):
    """Args for ``clients.create``."""

    name: NotBlankStr = Field(description="Client name")


class ClientsDeactivateArgs(_ClientIdArgs):
    """Args for ``clients.deactivate``."""


class ClientsGetSatisfactionArgs(_ClientIdArgs):
    """Args for ``clients.get_satisfaction``."""


class ArtifactsListArgs(PaginationFields):
    """Args for ``artifacts.list``."""

    task_id: NotBlankStr | None = Field(default=None, description="Filter by task")
    created_by: NotBlankStr | None = Field(
        default=None,
        description="Filter by creator",
    )
    type: NotBlankStr | None = Field(
        default=None,
        description="Filter by artifact type",
    )


class ArtifactsGetArgs(_ArgsBase):
    """Args for ``artifacts.get``."""

    artifact_id: NotBlankStr = Field(description="Artifact UUID")


class ArtifactsCreateArgs(_ArgsBase):
    """Args for ``artifacts.create``."""

    type: NotBlankStr = Field(description="Artifact type")
    content: str = Field(description="Artifact content")
    task_id: NotBlankStr | None = Field(default=None, description="Associated task")


class ArtifactsDeleteArgs(_ArgsBase):
    """Args for ``artifacts.delete``."""

    artifact_id: NotBlankStr = Field(description="Artifact UUID")


class OntologyListEntitiesArgs(PaginationFields):
    """Args for ``ontology.list_entities``."""


class _EntityNameArgs(_ArgsBase):
    """Mixin for tools keyed by ``entity_name``."""

    entity_name: NotBlankStr = Field(description="Entity name")


class OntologyGetEntityArgs(_EntityNameArgs):
    """Args for ``ontology.get_entity``."""


class OntologyGetRelationshipsArgs(_EntityNameArgs):
    """Args for ``ontology.get_relationships``."""


class OntologySearchArgs(_ArgsBase):
    """Args for ``ontology.search``."""

    query: NotBlankStr = Field(description="Search query")


# ─────────────────────────────────────────────────────────────────────
# Infrastructure: health, settings, providers, backup, audit, events,
# users, projects, requests, setup, simulations, template_packs,
# integration_health
# ─────────────────────────────────────────────────────────────────────


class HealthCheckArgs(_ArgsBase):
    """Args for ``health.check``: no fields."""


class SettingsListArgs(PaginationFields):
    """Args for ``settings.list``."""


class SettingsGetArgs(_ArgsBase):
    """Args for ``settings.get``."""

    key: NotBlankStr = Field(description="Setting key")


class SettingsUpdateArgs(_ArgsBase):
    """Args for ``settings.update``."""

    key: NotBlankStr = Field(description="Setting key")
    value: str = Field(description="New value")


class SettingsDeleteArgs(_ArgsBase):
    """Args for ``settings.delete``."""

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


class ProvidersTestConnectionArgs(_ProviderNameArgs):
    """Args for ``providers.test_connection``."""


class BackupCreateArgs(_ArgsBase):
    """Args for ``backup.create``: no fields."""


class BackupListArgs(_ArgsBase):
    """Args for ``backup.list``: no fields."""


class _BackupIdArgs(_ArgsBase):
    """Mixin for tools keyed by ``backup_id``."""

    backup_id: NotBlankStr = Field(description="Backup UUID")


class BackupGetArgs(_BackupIdArgs):
    """Args for ``backup.get``."""


class BackupDeleteArgs(_BackupIdArgs):
    """Args for ``backup.delete``."""


class BackupRestoreArgs(_BackupIdArgs):
    """Args for ``backup.restore``."""


class AuditListArgs(PaginationFields):
    """Args for ``audit.list``."""

    agent_id: NotBlankStr | None = Field(default=None, description="Filter by agent")
    tool_name: NotBlankStr | None = Field(default=None, description="Filter by tool")
    action_type: NotBlankStr | None = Field(
        default=None,
        description="Filter by action type",
    )
    verdict: NotBlankStr | None = Field(default=None, description="Filter by verdict")
    since: NotBlankStr | None = Field(default=None, description="Start datetime")
    until: NotBlankStr | None = Field(default=None, description="End datetime")


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


class UsersCreateArgs(_ArgsBase):
    """Args for ``users.create``."""

    username: NotBlankStr = Field(description="Username")
    role: NotBlankStr = Field(description="User role")


class UsersUpdateArgs(_UserIdArgs):
    """Args for ``users.update``."""

    updates: dict[str, object] = Field(description="Fields to update")


class UsersDeleteArgs(_UserIdArgs):
    """Args for ``users.delete``."""


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


class ProjectsDeleteArgs(_ProjectIdArgs):
    """Args for ``projects.delete``."""


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


class SetupInitializeArgs(_ArgsBase):
    """Args for ``setup.initialize``."""

    config: dict[str, object] = Field(
        default_factory=dict,
        description="Initial configuration",
    )


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


class TemplatePacksInstallArgs(_PackIdArgs):
    """Args for ``template_packs.install``."""


class TemplatePacksUninstallArgs(_PackIdArgs):
    """Args for ``template_packs.uninstall``."""


class IntegrationHealthGetAllArgs(_ArgsBase):
    """Args for ``integration_health.get_all``: no fields."""


class IntegrationHealthGetArgs(_ArgsBase):
    """Args for ``integration_health.get``."""

    integration_name: NotBlankStr = Field(description="Integration name")


# ─────────────────────────────────────────────────────────────────────
# Memory admin: fine-tuning, checkpoints, embedder, GDPR delete
# ─────────────────────────────────────────────────────────────────────


FineTuneBackend = Literal["in-process", "docker"]


class FineTuneExecutionConfig(_ArgsBase):
    """Optional runner-backend execution config for fine-tune tools.

    The ``image is required when backend == 'docker'`` cross-field
    constraint is enforced inside the Pydantic model in
    ``synthorg.memory.fine_tune_plan.FineTuneExecutionConfig``;
    we re-state the static shape here for the wire boundary.
    """

    backend: FineTuneBackend = Field(
        default="in-process",
        description="Execution backend",
    )
    image: NotBlankStr | None = Field(
        default=None,
        description="Container image (required when backend='docker')",
    )
    gpu_enabled: bool = Field(
        default=False,
        description="Request GPU passthrough (docker backend only)",
    )
    memory_limit: NotBlankStr = Field(
        default="8g",
        description="Container memory limit (Docker format)",
    )
    timeout_seconds: float = Field(
        default=7200.0,
        gt=0.0,
        description="Maximum wall-clock time for a single stage",
    )


class _FineTunePlanFields(_ArgsBase):
    """Shared shape for ``memory.start_fine_tune`` / ``run_preflight``."""

    source_dir: NotBlankStr = Field(description="Directory containing org documents")
    base_model: NotBlankStr | None = Field(
        default=None,
        description="Base model to fine-tune (None = active model)",
    )
    output_dir: NotBlankStr | None = Field(
        default=None,
        description="Checkpoint output directory (None = default)",
    )
    resume_run_id: NotBlankStr | None = Field(
        default=None,
        description="Resume a previous failed/cancelled run",
    )
    epochs: int | None = Field(
        default=None, ge=1, description="Override training epochs"
    )
    learning_rate: float | None = Field(
        default=None,
        gt=0.0,
        description="Override learning rate",
    )
    temperature: float | None = Field(
        default=None,
        gt=0.0,
        description="Override InfoNCE temperature",
    )
    top_k: int | None = Field(
        default=None,
        ge=1,
        description="Override hard negative count per query",
    )
    batch_size: int | None = Field(
        default=None,
        ge=1,
        description="Override training batch size",
    )
    validation_split: float | None = Field(
        default=None,
        gt=0.0,
        lt=1.0,
        description="Fraction held out for evaluation",
    )
    execution: FineTuneExecutionConfig | None = Field(
        default=None,
        description="Optional runner-backend execution config",
    )


class MemoryStartFineTuneArgs(_FineTunePlanFields):
    """Args for ``memory.start_fine_tune``."""


class MemoryResumeFineTuneArgs(_ArgsBase):
    """Args for ``memory.resume_fine_tune``."""

    run_id: NotBlankStr = Field(description="Run ID to resume")


class MemoryGetFineTuneStatusArgs(_ArgsBase):
    """Args for ``memory.get_fine_tune_status``: no fields."""


class MemoryCancelFineTuneArgs(DestructiveGuardrailFields):
    """Args for ``memory.cancel_fine_tune`` (destructive)."""


class MemoryRunPreflightArgs(_FineTunePlanFields):
    """Args for ``memory.run_preflight``."""


class MemoryListCheckpointsArgs(PaginationFields):
    """Args for ``memory.list_checkpoints``."""


class _CheckpointIdArgs(_ArgsBase):
    """Mixin for tools keyed by ``checkpoint_id``."""

    checkpoint_id: NotBlankStr = Field(description="Checkpoint UUID")


class MemoryDeployCheckpointArgs(_CheckpointIdArgs):
    """Args for ``memory.deploy_checkpoint``."""


class MemoryRollbackCheckpointArgs(_CheckpointIdArgs, DestructiveGuardrailFields):
    """Args for ``memory.rollback_checkpoint`` (destructive)."""


class MemoryDeleteCheckpointArgs(_CheckpointIdArgs, DestructiveGuardrailFields):
    """Args for ``memory.delete_checkpoint`` (destructive)."""


class MemoryListRunsArgs(PaginationFields):
    """Args for ``memory.list_runs``."""


class MemoryGetActiveEmbedderArgs(_ArgsBase):
    """Args for ``memory.get_active_embedder``: no fields."""


class MemoryDeleteEntryArgs(DestructiveGuardrailFields):
    """Args for ``memory.delete_entry`` (destructive, GDPR)."""

    agent_id: NotBlankStr = Field(description="Owning agent identifier")
    memory_id: NotBlankStr = Field(description="Backend-assigned memory identifier")


__all__ = [
    "ArtifactsCreateArgs",
    "ArtifactsDeleteArgs",
    "ArtifactsGetArgs",
    "ArtifactsListArgs",
    "AuditListArgs",
    "BackupCreateArgs",
    "BackupDeleteArgs",
    "BackupGetArgs",
    "BackupListArgs",
    "BackupRestoreArgs",
    "ClientsCreateArgs",
    "ClientsDeactivateArgs",
    "ClientsGetArgs",
    "ClientsGetSatisfactionArgs",
    "ClientsListArgs",
    "ConnectionsCheckHealthArgs",
    "ConnectionsCreateArgs",
    "ConnectionsDeleteArgs",
    "ConnectionsGetArgs",
    "ConnectionsListArgs",
    "EventsListArgs",
    "FineTuneBackend",
    "FineTuneExecutionConfig",
    "HealthCheckArgs",
    "IntegrationHealthGetAllArgs",
    "IntegrationHealthGetArgs",
    "McpCatalogGetArgs",
    "McpCatalogInstallArgs",
    "McpCatalogListArgs",
    "McpCatalogSearchArgs",
    "McpCatalogUninstallArgs",
    "MeetingsCreateArgs",
    "MeetingsDeleteArgs",
    "MeetingsGetArgs",
    "MeetingsListArgs",
    "MeetingsUpdateArgs",
    "MemoryCancelFineTuneArgs",
    "MemoryDeleteCheckpointArgs",
    "MemoryDeleteEntryArgs",
    "MemoryDeployCheckpointArgs",
    "MemoryGetActiveEmbedderArgs",
    "MemoryGetFineTuneStatusArgs",
    "MemoryListCheckpointsArgs",
    "MemoryListRunsArgs",
    "MemoryResumeFineTuneArgs",
    "MemoryRollbackCheckpointArgs",
    "MemoryRunPreflightArgs",
    "MemoryStartFineTuneArgs",
    "MessagesDeleteArgs",
    "MessagesGetArgs",
    "MessagesListArgs",
    "MessagesSendArgs",
    "OauthConfigureProviderArgs",
    "OauthListProvidersArgs",
    "OauthRemoveProviderArgs",
    "OntologyGetEntityArgs",
    "OntologyGetRelationshipsArgs",
    "OntologyListEntitiesArgs",
    "OntologySearchArgs",
    "ProjectsCreateArgs",
    "ProjectsDeleteArgs",
    "ProjectsGetArgs",
    "ProjectsListArgs",
    "ProjectsUpdateArgs",
    "ProvidersGetArgs",
    "ProvidersGetHealthArgs",
    "ProvidersListArgs",
    "ProvidersTestConnectionArgs",
    "RequestsCreateArgs",
    "RequestsGetArgs",
    "RequestsListArgs",
    "SettingsDeleteArgs",
    "SettingsGetArgs",
    "SettingsListArgs",
    "SettingsUpdateArgs",
    "SetupGetStatusArgs",
    "SetupInitializeArgs",
    "SimulationsCreateArgs",
    "SimulationsGetArgs",
    "SimulationsListArgs",
    "TemplatePacksGetArgs",
    "TemplatePacksInstallArgs",
    "TemplatePacksListArgs",
    "TemplatePacksUninstallArgs",
    "TunnelConnectArgs",
    "TunnelGetStatusArgs",
    "UsersCreateArgs",
    "UsersDeleteArgs",
    "UsersGetArgs",
    "UsersListArgs",
    "UsersUpdateArgs",
    "WebhooksCreateArgs",
    "WebhooksDeleteArgs",
    "WebhooksGetArgs",
    "WebhooksListArgs",
    "WebhooksUpdateArgs",
]
