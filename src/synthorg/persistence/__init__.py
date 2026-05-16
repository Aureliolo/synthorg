"""Pluggable persistence layer for operational data (see Memory design page).

Re-exports the protocol, repository protocols, config models, factory,
and error hierarchy so consumers can import from ``synthorg.persistence``
directly.
"""

from synthorg.core.persistence_errors import (
    ArtifactStorageFullError,
    ArtifactTooLargeError,
    DuplicateRecordError,
    MigrationError,
    PersistenceConnectionError,
    PersistenceError,
    QueryError,
    RecordNotFoundError,
)
from synthorg.persistence.agent_state_protocol import AgentStateRepository
from synthorg.persistence.artifact_protocol import ArtifactRepository
from synthorg.persistence.audit_protocol import AuditRepository
from synthorg.persistence.checkpoint_protocol import (
    CheckpointRepository,
    HeartbeatRepository,
)
from synthorg.persistence.config import PersistenceConfig, SQLiteConfig
from synthorg.persistence.connection_protocol import (
    ConnectionFilterSpec,
    ConnectionRepository,
    ConnectionSecretRepository,
    OAuthStateRepository,
    WebhookReceiptRepository,
)
from synthorg.persistence.cost_record_protocol import CostRecordRepository
from synthorg.persistence.decision_protocol import (
    DecisionRepository,
    DecisionRole,
)
from synthorg.persistence.factory import create_backend
from synthorg.persistence.message_protocol import MessageRepository
from synthorg.persistence.parked_context_protocol import ParkedContextRepository
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.settings_protocol import (
    SettingRow,
    SettingRowKey,
    SettingsRepository,
)
from synthorg.persistence.task_protocol import TaskRepository
from synthorg.persistence.training_protocol import (
    TrainingPlanFilterSpec,
    TrainingPlanRepository,
    TrainingResultRepository,
)
from synthorg.persistence.user_protocol import (
    ApiKeyRepository,
    UserRepository,
)
from synthorg.persistence.version_protocol import VersionRepository

__all__ = [
    "AgentStateRepository",
    "ApiKeyRepository",
    "ArtifactRepository",
    "ArtifactStorageFullError",
    "ArtifactTooLargeError",
    "AuditRepository",
    "CheckpointRepository",
    "ConnectionFilterSpec",
    "ConnectionRepository",
    "ConnectionSecretRepository",
    "CostRecordRepository",
    "DecisionRepository",
    "DecisionRole",
    "DuplicateRecordError",
    "HeartbeatRepository",
    "MessageRepository",
    "MigrationError",
    "OAuthStateRepository",
    "ParkedContextRepository",
    "PersistenceBackend",
    "PersistenceConfig",
    "PersistenceConnectionError",
    "PersistenceError",
    "ProjectRepository",
    "QueryError",
    "RecordNotFoundError",
    "SQLiteConfig",
    "SettingRow",
    "SettingRowKey",
    "SettingsRepository",
    "TaskRepository",
    "TrainingPlanFilterSpec",
    "TrainingPlanRepository",
    "TrainingResultRepository",
    "UserRepository",
    "VersionRepository",
    "WebhookReceiptRepository",
    "create_backend",
]
