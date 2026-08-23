"""Every enum member a column claims to hold must survive a write to it.

A ``CHECK (col IN (...))`` is a second copy of an enum, written in SQL, once per
backend. Nothing makes the copies agree with the original, and a member added to
the enum but not to the SQL raises on insert: the write never lands, and the
state the loop meant to record is the one state the archive cannot hold.

The conformance tier catches this too, but only against a live backend, so it
runs in CI on the pushed branch. These read the declared DDL directly, which
costs nothing and fails in the fast tier where the divergence is introduced.

Scope: every column whose CHECK list is exactly the member set of one enum, and
whose enum this file names. Columns whose CHECK list is a *subset* of an enum
are deliberately absent, because those are compound state predicates (a plan
whose status is one of two may carry a failure reason) rather than a copy of the
enum, and asserting equality against one would be asserting the wrong thing.
"""

import re
from enum import StrEnum
from pathlib import Path
from typing import Final

import pytest

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.budget.forecast_models import ForecastDecision
from synthorg.communication.conversation.enums import (
    ConversationRole,
    ConversationStatus,
)
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.billing_enums import BillingModel
from synthorg.core.deleted_entity import DeletedEntityKind
from synthorg.core.lifecycle_transition import LifecycleEntityKind
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.project_enums import EnvironmentType, GitBackendType
from synthorg.core.run_outcome import RunOutcome
from synthorg.core.task_enums import BlockedReason
from synthorg.docs_engine.enums import DocType
from synthorg.engine.agent_state import ExecutionStatus
from synthorg.engine.completion_oracle.review_models import CompletionOracleVerdict
from synthorg.engine.decisions import DecisionOutcome
from synthorg.engine.strategy.active_principle import (
    PrincipleEvolutionMode,
    ScopeKind,
)
from synthorg.engine.strategy.models import PrincipleSeverity
from synthorg.engine.workflow.enums import WorkflowExecutionStatus, WorkflowType
from synthorg.engine.workflow.sprint_lifecycle import SprintStatus
from synthorg.integrations.connections.models import (
    AuthMethod,
    ConnectionStatus,
    ConnectionType,
    WebhookIngestState,
)
from synthorg.knowledge.enums import ContentKind, SourceStatus, SourceType
from synthorg.memory.embedding.fine_tune import FineTuneStage
from synthorg.meta.charter.enums import CharterStatus
from synthorg.meta.chief_of_staff.enums import (
    ConversationInviteStatus,
    ConversationKind,
    ConversationParticipantStatus,
)
from synthorg.meta.models import RuleSeverity
from synthorg.meta.toolsmith.models import ToolBlueprintState, ToolSandboxBackend
from synthorg.ontology.models import EntitySource, EntityTier
from synthorg.persistence.code_execution_protocol import CodeExecutionPurpose
from synthorg.project_brain.models import BrainEntryKind
from synthorg.providers.enums import RecommendationStatus
from synthorg.research.enums import ResearchRunStatus
from synthorg.security.redteam.models import RedTeamVerdict
from synthorg.security.ssrf_violation import SsrfViolationStatus

pytestmark = pytest.mark.unit

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_SCHEMAS: Final[dict[str, Path]] = {
    "sqlite": _REPO_ROOT / "src" / "synthorg" / "persistence" / "sqlite" / "schema.sql",
    "postgres": (
        _REPO_ROOT / "src" / "synthorg" / "persistence" / "postgres" / "schema.sql"
    ),
}

#: ``(table, column, enum)`` for every column whose CHECK list must be exactly
#: the members of that enum. Keyed by table as well as column because a column
#: name is not unique across a schema: ``status`` appears on a dozen tables, and
#: a search that did not name the table would bind to whichever came first in
#: the file and then assert one table's enum against another's constraint.
_ENUM_CHECKED_COLUMNS: Final[tuple[tuple[str, str, type[StrEnum]], ...]] = (
    ("active_principles", "evolution_mode", PrincipleEvolutionMode),
    ("active_principles", "scope_kind", ScopeKind),
    ("active_principles", "severity", PrincipleSeverity),
    ("agent_states", "status", ExecutionStatus),
    ("approvals", "risk_level", ApprovalRiskLevel),
    ("approvals", "source", ApprovalSource),
    ("approvals", "status", ApprovalStatus),
    ("code_execution_record", "purpose", CodeExecutionPurpose),
    ("completion_oracle_reports", "verdict", CompletionOracleVerdict),
    ("connections", "auth_method", AuthMethod),
    ("connections", "connection_type", ConnectionType),
    ("connections", "health_status", ConnectionStatus),
    ("connections", "health_webhook_ingest", WebhookIngestState),
    ("conversation_invites", "status", ConversationInviteStatus),
    ("conversation_participants", "status", ConversationParticipantStatus),
    ("conversation_turns", "role", ConversationRole),
    ("conversations", "kind", ConversationKind),
    ("conversations", "status", ConversationStatus),
    ("cost_forecasts", "decision", ForecastDecision),
    ("cost_records", "billing_model", BillingModel),
    ("decision_records", "decision", DecisionOutcome),
    ("deleted_entities", "entity_kind", DeletedEntityKind),
    ("dynamic_tools", "sandbox_backend", ToolSandboxBackend),
    ("dynamic_tools", "state", ToolBlueprintState),
    ("entity_definitions", "source", EntitySource),
    ("entity_definitions", "tier", EntityTier),
    ("fine_tune_runs", "stage", FineTuneStage),
    ("knowledge_chunk_provenance", "content_kind", ContentKind),
    ("knowledge_sources", "source_type", SourceType),
    ("knowledge_sources", "status", SourceStatus),
    ("lifecycle_transitions", "entity_kind", LifecycleEntityKind),
    ("org_alerts", "severity", RuleSeverity),
    ("plans", "status", PlanStatus),
    ("project_brain_entries", "entry_kind", BrainEntryKind),
    ("project_charters", "status", CharterStatus),
    ("project_docs", "doc_type", DocType),
    ("project_environments", "environment_type", EnvironmentType),
    ("project_workspaces", "git_backend_kind", GitBackendType),
    ("projects", "autonomy_mode", AutonomyLevel),
    ("red_team_reports", "verdict", RedTeamVerdict),
    ("research_runs", "status", ResearchRunStatus),
    ("sprints", "status", SprintStatus),
    ("ssrf_violations", "status", SsrfViolationStatus),
    ("subworkflows", "workflow_type", WorkflowType),
    ("task_metrics", "run_outcome", RunOutcome),
    ("tasks", "blocked_reason", BlockedReason),
    ("upgrade_recommendations", "status", RecommendationStatus),
    ("workflow_definitions", "workflow_type", WorkflowType),
    ("workflow_executions", "status", WorkflowExecutionStatus),
)


def _table_body(ddl: str, table: str) -> str | None:
    """The column list of one ``CREATE TABLE``.

    Returns:
        The body between the outer parentheses, or ``None`` when the schema
        declares no such table.
    """
    match = re.search(
        rf"CREATE TABLE (?:IF NOT EXISTS )?{re.escape(table)}\s*\((?P<body>.*?)\n\);",
        ddl,
        re.IGNORECASE | re.DOTALL,
    )
    return None if match is None else match.group("body")


def _checked_values(ddl: str, table: str, column: str) -> set[str]:
    """The quoted values the column's own ``CHECK (<column> IN (...))`` admits.

    Anchored to the column's declaration, not merely to the table: a table-level
    CHECK naming the same column expresses a compound state rule over a subset
    of the enum, and matching one of those would assert the wrong list.

    Returns:
        The admitted values, or an empty set when the column has no such CHECK.
    """
    body = _table_body(ddl, table)
    if body is None:
        return set()
    match = re.search(
        rf"\b{re.escape(column)}\s+\w+[^,()]*?\bCHECK\s*\(\s*"
        rf"{re.escape(column)}\s+IN\s*\((?P<values>[^)]*)\)",
        body,
        re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return set()
    return set(re.findall(r"'([^']*)'", match.group("values")))


@pytest.mark.parametrize("backend", sorted(_SCHEMAS))
@pytest.mark.parametrize(
    ("table", "column", "enum"),
    _ENUM_CHECKED_COLUMNS,
    ids=[f"{table}.{column}" for table, column, _ in _ENUM_CHECKED_COLUMNS],
)
def test_check_constraint_admits_every_enum_member(
    backend: str, table: str, column: str, enum: type[StrEnum]
) -> None:
    """The SQL copy of an enum admits exactly what the enum declares."""
    ddl = _SCHEMAS[backend].read_text(encoding="utf-8")
    admitted = _checked_values(ddl, table, column)

    assert admitted, f"{backend}: no CHECK ... IN (...) found for {table}.{column}"
    # Both directions: a missing member is a write that raises, and a surplus
    # value is a row the domain model would then refuse to parse back.
    assert admitted == {member.value for member in enum}


def test_no_declared_pair_names_a_table_the_schema_lacks() -> None:
    """A stale entry would otherwise assert against an empty body and pass."""
    ddl = _SCHEMAS["sqlite"].read_text(encoding="utf-8")
    missing = [
        table
        for table, _, _ in _ENUM_CHECKED_COLUMNS
        if _table_body(ddl, table) is None
    ]
    assert missing == []
