"""Tests for observability event name constants."""

import importlib
import pkgutil
import re

import pytest

from synthorg.observability import events
from synthorg.observability.events.budget import BUDGET_RECORD_ADDED
from synthorg.observability.events.checkpoint import (
    CHECKPOINT_RECOVERY_START,
    CHECKPOINT_SAVED,
    HEARTBEAT_UPDATED,
)
from synthorg.observability.events.classification import (
    CLASSIFICATION_COMPLETE,
    CLASSIFICATION_ERROR,
    CLASSIFICATION_FINDING,
    CLASSIFICATION_SKIPPED,
    CLASSIFICATION_START,
    DETECTOR_COMPLETE,
    DETECTOR_ERROR,
    DETECTOR_START,
)
from synthorg.observability.events.communication import (
    COMM_BUS_ALREADY_RUNNING,
    COMM_BUS_NOT_RUNNING,
    COMM_BUS_STARTED,
    COMM_DISPATCH_NO_DISPATCHER,
    COMM_HANDLER_DEREGISTER_MISS,
    COMM_MESSAGE_PUBLISHED,
)
from synthorg.observability.events.config import (
    CONFIG_LOADED,
    CONFIG_PARSE_FAILED,
    CONFIG_VALIDATION_FAILED,
)
from synthorg.observability.events.conflict import (
    CONFLICT_AMBIGUOUS_RESULT,
    CONFLICT_AUTHORITY_DECIDED,
    CONFLICT_AUTHORITY_FALLBACK,
    CONFLICT_CROSS_DEPARTMENT,
    CONFLICT_DEBATE_JUDGE_DECIDED,
    CONFLICT_DEBATE_STARTED,
    CONFLICT_DETECTED,
    CONFLICT_DISSENT_QUERIED,
    CONFLICT_DISSENT_RECORDED,
    CONFLICT_ESCALATED,
    CONFLICT_HIERARCHY_ERROR,
    CONFLICT_HUMAN_ESCALATION_STUB,
    CONFLICT_HYBRID_AUTO_RESOLVED,
    CONFLICT_HYBRID_REVIEW,
    CONFLICT_LCM_LOOKUP,
    CONFLICT_NO_RESOLVER,
    CONFLICT_RESOLUTION_FAILED,
    CONFLICT_RESOLUTION_STARTED,
    CONFLICT_RESOLVED,
    CONFLICT_STRATEGY_ERROR,
    CONFLICT_VALIDATION_ERROR,
)
from synthorg.observability.events.delegation import (
    DELEGATION_CREATED,
    DELEGATION_HIERARCHY_BUILT,
    DELEGATION_HIERARCHY_CYCLE,
    DELEGATION_LOOP_BLOCKED,
    DELEGATION_LOOP_ESCALATED,
    DELEGATION_REQUESTED,
    DELEGATION_RESULT_SENT,
)
from synthorg.observability.events.execution import EXECUTION_TASK_CREATED
from synthorg.observability.events.git import (
    GIT_CLONE_URL_REJECTED,
    GIT_COMMAND_FAILED,
    GIT_COMMAND_START,
    GIT_COMMAND_SUCCESS,
    GIT_COMMAND_TIMEOUT,
    GIT_REF_INJECTION_BLOCKED,
    GIT_WORKSPACE_VIOLATION,
)
from synthorg.observability.events.prompt import PROMPT_BUILD_START
from synthorg.observability.events.provider import (
    PROVIDER_CALL_START,
    PROVIDER_REGISTRY_BUILT,
)
from synthorg.observability.events.role import ROLE_LOOKUP_MISS
from synthorg.observability.events.routing import ROUTING_DECISION_MADE
from synthorg.observability.events.sandbox import (
    SANDBOX_CLEANUP,
    SANDBOX_ENV_FILTERED,
    SANDBOX_EXECUTE_FAILED,
    SANDBOX_EXECUTE_START,
    SANDBOX_EXECUTE_SUCCESS,
    SANDBOX_EXECUTE_TIMEOUT,
    SANDBOX_HEALTH_CHECK,
    SANDBOX_KILL_FAILED,
    SANDBOX_PATH_FALLBACK,
    SANDBOX_SPAWN_FAILED,
    SANDBOX_WORKSPACE_VIOLATION,
)
from synthorg.observability.events.task import TASK_STATUS_CHANGED
from synthorg.observability.events.task_assignment import (
    TASK_ASSIGNMENT_AGENT_SCORED,
    TASK_ASSIGNMENT_AGENT_SELECTED,
    TASK_ASSIGNMENT_COMPLETE,
    TASK_ASSIGNMENT_FAILED,
    TASK_ASSIGNMENT_MANUAL_VALIDATED,
    TASK_ASSIGNMENT_NO_ELIGIBLE,
    TASK_ASSIGNMENT_STARTED,
    TASK_ASSIGNMENT_WORKLOAD_BALANCED,
)
from synthorg.observability.events.template import (
    TEMPLATE_RENDER_START,
    TEMPLATE_RENDER_SUCCESS,
)
from synthorg.observability.events.tool import TOOL_INVOKE_START
from synthorg.observability.events.workspace import (
    WORKSPACE_GROUP_MERGE_COMPLETE,
    WORKSPACE_GROUP_MERGE_START,
    WORKSPACE_GROUP_SETUP_COMPLETE,
    WORKSPACE_GROUP_SETUP_FAILED,
    WORKSPACE_GROUP_SETUP_START,
    WORKSPACE_GROUP_TEARDOWN_COMPLETE,
    WORKSPACE_GROUP_TEARDOWN_START,
    WORKSPACE_LIMIT_REACHED,
    WORKSPACE_MERGE_ABORT_FAILED,
    WORKSPACE_MERGE_COMPLETE,
    WORKSPACE_MERGE_CONFLICT,
    WORKSPACE_MERGE_FAILED,
    WORKSPACE_MERGE_START,
    WORKSPACE_SETUP_COMPLETE,
    WORKSPACE_SETUP_FAILED,
    WORKSPACE_SETUP_START,
    WORKSPACE_SORT_WORKSPACES_APPENDED,
    WORKSPACE_TEARDOWN_COMPLETE,
    WORKSPACE_TEARDOWN_FAILED,
    WORKSPACE_TEARDOWN_START,
)

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]

_DOT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


def _all_event_names() -> list[tuple[str, str]]:
    """Return (attr_name, value) for every public string constant."""
    result: list[tuple[str, str]] = []
    for info in pkgutil.iter_modules(events.__path__):
        mod = importlib.import_module(f"synthorg.observability.events.{info.name}")
        for attr in dir(mod):
            if attr.startswith("_"):
                continue
            val = getattr(mod, attr)
            if attr.isupper() and isinstance(val, str):
                result.append((attr, val))
    return result


class TestEventConstants:
    def test_all_are_strings(self) -> None:
        for attr, val in _all_event_names():
            assert isinstance(val, str), f"{attr} is not a string"

    def test_follow_dot_pattern(self) -> None:
        for attr, val in _all_event_names():
            assert _DOT_PATTERN.match(val), (
                f"{attr}={val!r} does not match domain.subject.qualifier pattern"
            )

    def test_no_duplicates(self) -> None:
        values = [val for _, val in _all_event_names()]
        assert len(values) == len(set(values)), (
            f"Duplicate event names: {[v for v in values if values.count(v) > 1]}"
        )

    def test_has_at_least_20_events(self) -> None:
        assert len(_all_event_names()) >= 20

    def test_all_domain_modules_discovered(self) -> None:
        """Every expected domain module is found by pkgutil discovery."""
        expected = {
            "api",
            "approval_gate",
            "autonomy",
            "budget",
            "cfo",
            "classification",
            "code_runner",
            "communication",
            "company",
            "config",
            "conflict",
            "consolidation",
            "coordination",
            "correlation",
            "decomposition",
            "delegation",
            "docker",
            "execution",
            "git",
            "hr",
            "mcp",
            "meeting",
            "memory",
            "org_memory",
            "parallel",
            "performance",
            "persistence",
            "personality",
            "prompt",
            "provider",
            "quota",
            "role",
            "routing",
            "sandbox",
            "security",
            "task",
            "task_assignment",
            "task_engine",
            "task_routing",
            "template",
            "timeout",
            "tool",
            "workspace",
            "trust",
            "promotion",
            "checkpoint",
        }
        discovered = {info.name for info in pkgutil.iter_modules(events.__path__)}
        assert discovered == expected

    def test_config_events_exist(self) -> None:
        assert CONFIG_LOADED == "config.load.success"
        assert CONFIG_PARSE_FAILED == "config.parse.failed"
        assert CONFIG_VALIDATION_FAILED == "config.validation.failed"

    def test_provider_events_exist(self) -> None:
        assert PROVIDER_CALL_START == "provider.call.start"
        assert PROVIDER_REGISTRY_BUILT == "provider.registry.built"

    def test_task_events_exist(self) -> None:
        assert TASK_STATUS_CHANGED == "task.status.changed"

    def test_template_events_exist(self) -> None:
        assert TEMPLATE_RENDER_START == "template.render.start"
        assert TEMPLATE_RENDER_SUCCESS == "template.render.success"

    def test_role_events_exist(self) -> None:
        assert ROLE_LOOKUP_MISS == "role.lookup.miss"

    def test_budget_events_exist(self) -> None:
        assert BUDGET_RECORD_ADDED == "budget.record.added"

    def test_checkpoint_events_exist(self) -> None:
        assert CHECKPOINT_SAVED == "checkpoint.saved"
        assert HEARTBEAT_UPDATED == "heartbeat.updated"
        assert CHECKPOINT_RECOVERY_START == "checkpoint.recovery.start"

    def test_execution_events_exist(self) -> None:
        assert EXECUTION_TASK_CREATED == "execution.task.created"

    def test_routing_events_exist(self) -> None:
        assert ROUTING_DECISION_MADE == "routing.decision.made"

    def test_prompt_events_exist(self) -> None:
        assert PROMPT_BUILD_START == "prompt.build.start"

    def test_git_events_exist(self) -> None:
        assert GIT_COMMAND_START == "git.command.start"
        assert GIT_COMMAND_SUCCESS == "git.command.success"
        assert GIT_COMMAND_FAILED == "git.command.failed"
        assert GIT_COMMAND_TIMEOUT == "git.command.timeout"
        assert GIT_WORKSPACE_VIOLATION == "git.workspace.violation"
        assert GIT_CLONE_URL_REJECTED == "git.clone.url_rejected"
        assert GIT_REF_INJECTION_BLOCKED == "git.ref.injection_blocked"

    def test_sandbox_events_exist(self) -> None:
        assert SANDBOX_EXECUTE_START == "sandbox.execute.start"
        assert SANDBOX_EXECUTE_SUCCESS == "sandbox.execute.success"
        assert SANDBOX_EXECUTE_FAILED == "sandbox.execute.failed"
        assert SANDBOX_EXECUTE_TIMEOUT == "sandbox.execute.timeout"
        assert SANDBOX_SPAWN_FAILED == "sandbox.spawn.failed"
        assert SANDBOX_ENV_FILTERED == "sandbox.env.filtered"
        assert SANDBOX_WORKSPACE_VIOLATION == "sandbox.workspace.violation"
        assert SANDBOX_CLEANUP == "sandbox.cleanup"
        assert SANDBOX_PATH_FALLBACK == "sandbox.path.fallback"
        assert SANDBOX_HEALTH_CHECK == "sandbox.health_check"
        assert SANDBOX_KILL_FAILED == "sandbox.kill.failed"

    def test_communication_events_exist(self) -> None:
        assert COMM_BUS_STARTED == "communication.bus.started"
        assert COMM_BUS_ALREADY_RUNNING == "communication.bus.already_running"
        assert COMM_BUS_NOT_RUNNING == "communication.bus.not_running"
        assert COMM_MESSAGE_PUBLISHED == "communication.message.published"
        assert COMM_HANDLER_DEREGISTER_MISS == "communication.handler.deregister_miss"
        assert COMM_DISPATCH_NO_DISPATCHER == "communication.dispatch.no_dispatcher"

    def test_delegation_events_exist(self) -> None:
        assert DELEGATION_REQUESTED == "delegation.requested"
        assert DELEGATION_CREATED == "delegation.created"
        assert DELEGATION_RESULT_SENT == "delegation.result_sent"
        assert DELEGATION_LOOP_BLOCKED == "delegation.loop.blocked"
        assert DELEGATION_LOOP_ESCALATED == "delegation.loop.escalated"
        assert DELEGATION_HIERARCHY_BUILT == "delegation.hierarchy.built"
        assert DELEGATION_HIERARCHY_CYCLE == "delegation.hierarchy.cycle"

    def test_conflict_events_exist(self) -> None:
        assert CONFLICT_DETECTED == "conflict.detected"
        assert CONFLICT_RESOLUTION_STARTED == "conflict.resolution.started"
        assert CONFLICT_RESOLVED == "conflict.resolved"
        assert CONFLICT_RESOLUTION_FAILED == "conflict.resolution.failed"
        assert CONFLICT_ESCALATED == "conflict.escalated"
        assert CONFLICT_DISSENT_RECORDED == "conflict.dissent.recorded"
        assert CONFLICT_AUTHORITY_DECIDED == "conflict.authority.decided"
        assert CONFLICT_DEBATE_STARTED == "conflict.debate.started"
        assert CONFLICT_DEBATE_JUDGE_DECIDED == "conflict.debate.judge_decided"
        assert CONFLICT_HYBRID_REVIEW == "conflict.hybrid.review"
        assert CONFLICT_HYBRID_AUTO_RESOLVED == "conflict.hybrid.auto_resolved"
        assert CONFLICT_HUMAN_ESCALATION_STUB == "conflict.human.escalation_stub"
        assert CONFLICT_CROSS_DEPARTMENT == "conflict.cross_department"
        assert CONFLICT_LCM_LOOKUP == "conflict.lcm_lookup"
        assert CONFLICT_DISSENT_QUERIED == "conflict.dissent.queried"
        assert CONFLICT_VALIDATION_ERROR == "conflict.validation.error"
        assert CONFLICT_NO_RESOLVER == "conflict.no_resolver"
        assert CONFLICT_AUTHORITY_FALLBACK == "conflict.authority_fallback"
        assert CONFLICT_AMBIGUOUS_RESULT == "conflict.ambiguous_result"
        assert CONFLICT_HIERARCHY_ERROR == "conflict.hierarchy.error"
        assert CONFLICT_STRATEGY_ERROR == "conflict.strategy.error"

    def test_task_assignment_events_exist(self) -> None:
        assert TASK_ASSIGNMENT_STARTED == "task_assignment.started"
        assert TASK_ASSIGNMENT_COMPLETE == "task_assignment.complete"
        assert TASK_ASSIGNMENT_FAILED == "task_assignment.failed"
        assert TASK_ASSIGNMENT_NO_ELIGIBLE == "task_assignment.no_eligible"
        assert TASK_ASSIGNMENT_AGENT_SCORED == "task_assignment.agent.scored"
        assert TASK_ASSIGNMENT_AGENT_SELECTED == "task_assignment.agent.selected"
        assert TASK_ASSIGNMENT_MANUAL_VALIDATED == "task_assignment.manual.validated"
        assert TASK_ASSIGNMENT_WORKLOAD_BALANCED == "task_assignment.workload.balanced"

    def test_tool_events_exist(self) -> None:
        assert TOOL_INVOKE_START == "tool.invoke.start"

    def test_meeting_events_exist(self) -> None:
        from synthorg.observability.events.meeting import (
            MEETING_ACTION_ITEM_EXTRACTED,
            MEETING_AGENT_CALLED,
            MEETING_AGENT_RESPONDED,
            MEETING_BUDGET_EXHAUSTED,
            MEETING_COMPLETED,
            MEETING_CONFLICT_DETECTED,
            MEETING_CONTRIBUTION_RECORDED,
            MEETING_FAILED,
            MEETING_PHASE_COMPLETED,
            MEETING_PHASE_STARTED,
            MEETING_PROTOCOL_NOT_FOUND,
            MEETING_STARTED,
            MEETING_SUMMARY_GENERATED,
            MEETING_SUMMARY_SKIPPED,
            MEETING_SYNTHESIS_SKIPPED,
            MEETING_TASK_CREATED,
            MEETING_TASK_CREATION_FAILED,
            MEETING_TOKENS_RECORDED,
            MEETING_VALIDATION_FAILED,
        )

        assert MEETING_STARTED == "meeting.lifecycle.started"
        assert MEETING_COMPLETED == "meeting.lifecycle.completed"
        assert MEETING_FAILED == "meeting.lifecycle.failed"
        assert MEETING_BUDGET_EXHAUSTED == "meeting.lifecycle.budget_exhausted"
        assert MEETING_PHASE_STARTED == "meeting.phase.started"
        assert MEETING_PHASE_COMPLETED == "meeting.phase.completed"
        assert MEETING_AGENT_CALLED == "meeting.agent.called"
        assert MEETING_AGENT_RESPONDED == "meeting.agent.responded"
        assert MEETING_CONTRIBUTION_RECORDED == "meeting.contribution.recorded"
        assert MEETING_CONFLICT_DETECTED == "meeting.conflict.detected"
        assert MEETING_SUMMARY_GENERATED == "meeting.summary.generated"
        assert MEETING_ACTION_ITEM_EXTRACTED == "meeting.action_item.extracted"
        assert MEETING_TASK_CREATED == "meeting.task.created"
        assert MEETING_TASK_CREATION_FAILED == "meeting.task.creation_failed"
        assert MEETING_VALIDATION_FAILED == "meeting.validation.failed"
        assert MEETING_PROTOCOL_NOT_FOUND == "meeting.protocol.not_found"
        assert MEETING_SYNTHESIS_SKIPPED == "meeting.synthesis.skipped"
        assert MEETING_SUMMARY_SKIPPED == "meeting.summary.skipped"
        assert MEETING_TOKENS_RECORDED == "meeting.tokens.recorded"

    def test_workspace_events_exist(self) -> None:
        assert WORKSPACE_SETUP_START == "workspace.setup.start"
        assert WORKSPACE_SETUP_COMPLETE == "workspace.setup.complete"
        assert WORKSPACE_SETUP_FAILED == "workspace.setup.failed"
        assert WORKSPACE_MERGE_START == "workspace.merge.start"
        assert WORKSPACE_MERGE_COMPLETE == "workspace.merge.complete"
        assert WORKSPACE_MERGE_CONFLICT == "workspace.merge.conflict"
        assert WORKSPACE_MERGE_FAILED == "workspace.merge.failed"
        assert WORKSPACE_MERGE_ABORT_FAILED == "workspace.merge.abort.failed"
        assert WORKSPACE_TEARDOWN_START == "workspace.teardown.start"
        assert WORKSPACE_TEARDOWN_COMPLETE == "workspace.teardown.complete"
        assert WORKSPACE_TEARDOWN_FAILED == "workspace.teardown.failed"
        assert WORKSPACE_LIMIT_REACHED == "workspace.limit.reached"
        assert WORKSPACE_GROUP_MERGE_START == "workspace.group.merge.start"
        assert WORKSPACE_GROUP_MERGE_COMPLETE == "workspace.group.merge.complete"
        assert WORKSPACE_GROUP_SETUP_START == "workspace.group.setup.start"
        assert WORKSPACE_GROUP_SETUP_COMPLETE == "workspace.group.setup.complete"
        assert WORKSPACE_GROUP_TEARDOWN_START == "workspace.group.teardown.start"
        assert WORKSPACE_GROUP_TEARDOWN_COMPLETE == "workspace.group.teardown.complete"
        assert (
            WORKSPACE_SORT_WORKSPACES_APPENDED == "workspace.sort.workspaces.appended"
        )
        assert WORKSPACE_GROUP_SETUP_FAILED == "workspace.group.setup.failed"

    @pytest.mark.parametrize(
        ("constant_name", "expected"),
        [
            ("MAINTENANCE_START", "consolidation.maintenance.start"),
            ("MAINTENANCE_COMPLETE", "consolidation.maintenance.complete"),
            ("MAINTENANCE_FAILED", "consolidation.maintenance.failed"),
            ("CONSOLIDATION_START", "consolidation.run.start"),
            ("CONSOLIDATION_COMPLETE", "consolidation.run.complete"),
            ("CONSOLIDATION_FAILED", "consolidation.run.failed"),
            ("CONSOLIDATION_SKIPPED", "consolidation.run.skipped"),
            ("STRATEGY_START", "consolidation.strategy.start"),
            ("STRATEGY_COMPLETE", "consolidation.strategy.complete"),
            ("RETENTION_CLEANUP_START", "consolidation.retention.start"),
            ("RETENTION_CLEANUP_COMPLETE", "consolidation.retention.complete"),
            ("RETENTION_CLEANUP_FAILED", "consolidation.retention.failed"),
            ("RETENTION_DELETE_SKIPPED", "consolidation.retention.delete_skipped"),
            ("ARCHIVAL_ENTRY_STORED", "consolidation.archival.stored"),
            ("ARCHIVAL_SEARCH_COMPLETE", "consolidation.archival.search_complete"),
            ("ARCHIVAL_FAILED", "consolidation.archival.failed"),
            ("MAX_MEMORIES_ENFORCED", "consolidation.max_memories.enforced"),
            (
                "MAX_MEMORIES_ENFORCE_FAILED",
                "consolidation.max_memories.failed",
            ),
        ],
    )
    def test_consolidation_events_exist(
        self,
        constant_name: str,
        expected: str,
    ) -> None:
        from synthorg.observability.events import consolidation as mod

        assert getattr(mod, constant_name) == expected

    @pytest.mark.parametrize(
        ("constant_name", "expected"),
        [
            ("ORG_MEMORY_QUERY_START", "org_memory.query.start"),
            ("ORG_MEMORY_QUERY_COMPLETE", "org_memory.query.complete"),
            ("ORG_MEMORY_QUERY_FAILED", "org_memory.query.failed"),
            ("ORG_MEMORY_WRITE_START", "org_memory.write.start"),
            ("ORG_MEMORY_WRITE_COMPLETE", "org_memory.write.complete"),
            ("ORG_MEMORY_WRITE_DENIED", "org_memory.write.denied"),
            ("ORG_MEMORY_WRITE_FAILED", "org_memory.write.failed"),
            ("ORG_MEMORY_POLICIES_LISTED", "org_memory.policies.listed"),
            ("ORG_MEMORY_BACKEND_CREATED", "org_memory.backend.created"),
            ("ORG_MEMORY_CONNECT_FAILED", "org_memory.store.connect_failed"),
            (
                "ORG_MEMORY_DISCONNECT_FAILED",
                "org_memory.store.disconnect_failed",
            ),
            ("ORG_MEMORY_NOT_CONNECTED", "org_memory.store.not_connected"),
            (
                "ORG_MEMORY_ROW_PARSE_FAILED",
                "org_memory.store.row_parse_failed",
            ),
            ("ORG_MEMORY_CONFIG_INVALID", "org_memory.config.invalid"),
            ("ORG_MEMORY_MODEL_INVALID", "org_memory.model.invalid"),
        ],
    )
    def test_org_memory_events_exist(
        self,
        constant_name: str,
        expected: str,
    ) -> None:
        from synthorg.observability.events import org_memory as mod

        assert getattr(mod, constant_name) == expected

    @pytest.mark.parametrize(
        ("constant_name", "expected"),
        [
            ("MEMORY_BACKEND_CONNECTING", "memory.backend.connecting"),
            ("MEMORY_BACKEND_CONNECTED", "memory.backend.connected"),
            ("MEMORY_BACKEND_CONNECTION_FAILED", "memory.backend.connection_failed"),
            ("MEMORY_BACKEND_DISCONNECTING", "memory.backend.disconnecting"),
            ("MEMORY_BACKEND_DISCONNECTED", "memory.backend.disconnected"),
            ("MEMORY_BACKEND_HEALTH_CHECK", "memory.backend.health_check"),
            ("MEMORY_BACKEND_CREATED", "memory.backend.created"),
            ("MEMORY_BACKEND_UNKNOWN", "memory.backend.unknown"),
            ("MEMORY_BACKEND_NOT_CONNECTED", "memory.backend.not_connected"),
            ("MEMORY_ENTRY_STORED", "memory.entry.stored"),
            ("MEMORY_ENTRY_STORE_FAILED", "memory.entry.store_failed"),
            ("MEMORY_ENTRY_RETRIEVED", "memory.entry.retrieved"),
            ("MEMORY_ENTRY_RETRIEVAL_FAILED", "memory.entry.retrieval_failed"),
            ("MEMORY_ENTRY_FETCHED", "memory.entry.fetched"),
            ("MEMORY_ENTRY_FETCH_FAILED", "memory.entry.fetch_failed"),
            ("MEMORY_ENTRY_DELETED", "memory.entry.deleted"),
            ("MEMORY_ENTRY_DELETE_FAILED", "memory.entry.delete_failed"),
            ("MEMORY_ENTRY_COUNTED", "memory.entry.counted"),
            ("MEMORY_ENTRY_COUNT_FAILED", "memory.entry.count_failed"),
            ("MEMORY_SHARED_PUBLISHED", "memory.shared.published"),
            ("MEMORY_SHARED_PUBLISH_FAILED", "memory.shared.publish_failed"),
            ("MEMORY_SHARED_SEARCHED", "memory.shared.searched"),
            ("MEMORY_SHARED_SEARCH_FAILED", "memory.shared.search_failed"),
            ("MEMORY_SHARED_RETRACTED", "memory.shared.retracted"),
            ("MEMORY_SHARED_RETRACT_FAILED", "memory.shared.retract_failed"),
            ("MEMORY_MODEL_INVALID", "memory.model.invalid"),
        ],
    )
    def test_memory_events_exist(self, constant_name: str, expected: str) -> None:
        from synthorg.observability.events import memory as mod

        assert getattr(mod, constant_name) == expected

    @pytest.mark.parametrize(
        ("constant_name", "expected"),
        [
            ("PERSISTENCE_BACKEND_CONNECTING", "persistence.backend.connecting"),
            ("PERSISTENCE_BACKEND_CONNECTED", "persistence.backend.connected"),
            ("PERSISTENCE_BACKEND_DISCONNECTING", "persistence.backend.disconnecting"),
            ("PERSISTENCE_BACKEND_DISCONNECTED", "persistence.backend.disconnected"),
            ("PERSISTENCE_BACKEND_HEALTH_CHECK", "persistence.backend.health_check"),
            (
                "PERSISTENCE_BACKEND_NOT_CONNECTED",
                "persistence.backend.not_connected",
            ),
            ("PERSISTENCE_MIGRATION_STARTED", "persistence.migration.started"),
            ("PERSISTENCE_MIGRATION_COMPLETED", "persistence.migration.completed"),
            ("PERSISTENCE_MIGRATION_SKIPPED", "persistence.migration.skipped"),
            ("PERSISTENCE_TASK_SAVED", "persistence.task.saved"),
            ("PERSISTENCE_TASK_FETCHED", "persistence.task.fetched"),
            ("PERSISTENCE_TASK_LISTED", "persistence.task.listed"),
            ("PERSISTENCE_TASK_DELETED", "persistence.task.deleted"),
            (
                "PERSISTENCE_TASK_DESERIALIZE_FAILED",
                "persistence.task.deserialize_failed",
            ),
            ("PERSISTENCE_COST_RECORD_SAVED", "persistence.cost_record.saved"),
            (
                "PERSISTENCE_COST_RECORD_QUERIED",
                "persistence.cost_record.queried",
            ),
            (
                "PERSISTENCE_COST_RECORD_AGGREGATED",
                "persistence.cost_record.aggregated",
            ),
            ("PERSISTENCE_MESSAGE_SAVED", "persistence.message.saved"),
            (
                "PERSISTENCE_MESSAGE_HISTORY_FETCHED",
                "persistence.message.history_fetched",
            ),
            (
                "PERSISTENCE_MESSAGE_DESERIALIZE_FAILED",
                "persistence.message.deserialize_failed",
            ),
        ],
    )
    def test_persistence_events_exist(self, constant_name: str, expected: str) -> None:
        from synthorg.observability.events import persistence as mod

        assert getattr(mod, constant_name) == expected

    def test_autonomy_events_exist(self) -> None:
        from synthorg.observability.events.autonomy import (
            AUTONOMY_ACTION_AUTO_APPROVED,
            AUTONOMY_ACTION_HUMAN_REQUIRED,
            AUTONOMY_DOWNGRADE_TRIGGERED,
            AUTONOMY_PRESET_EXPANDED,
            AUTONOMY_PROMOTION_DENIED,
            AUTONOMY_PROMOTION_REQUESTED,
            AUTONOMY_RECOVERY_REQUESTED,
            AUTONOMY_RESOLVED,
            AUTONOMY_SENIORITY_VIOLATION,
        )

        assert AUTONOMY_RESOLVED == "autonomy.resolved"
        assert AUTONOMY_PROMOTION_REQUESTED == "autonomy.promotion.requested"
        assert AUTONOMY_PROMOTION_DENIED == "autonomy.promotion.denied"
        assert AUTONOMY_DOWNGRADE_TRIGGERED == "autonomy.downgrade.triggered"
        assert AUTONOMY_RECOVERY_REQUESTED == "autonomy.recovery.requested"
        assert AUTONOMY_SENIORITY_VIOLATION == "autonomy.seniority.violation"
        assert AUTONOMY_PRESET_EXPANDED == "autonomy.preset.expanded"
        assert AUTONOMY_ACTION_AUTO_APPROVED == "autonomy.action.auto_approved"
        assert AUTONOMY_ACTION_HUMAN_REQUIRED == "autonomy.action.human_required"

    def test_timeout_events_exist(self) -> None:
        from synthorg.observability.events.timeout import (
            TIMEOUT_AUTO_APPROVED,
            TIMEOUT_AUTO_DENIED,
            TIMEOUT_CONTEXT_PARKED,
            TIMEOUT_CONTEXT_RESUMED,
            TIMEOUT_ESCALATED,
            TIMEOUT_POLICY_EVALUATED,
            TIMEOUT_UNKNOWN_ACTION_TYPE,
            TIMEOUT_WAITING,
        )

        assert TIMEOUT_POLICY_EVALUATED == "timeout.policy.evaluated"
        assert TIMEOUT_AUTO_APPROVED == "timeout.auto_approved"
        assert TIMEOUT_AUTO_DENIED == "timeout.auto_denied"
        assert TIMEOUT_ESCALATED == "timeout.escalated"
        assert TIMEOUT_WAITING == "timeout.waiting"
        assert TIMEOUT_CONTEXT_PARKED == "timeout.context.parked"
        assert TIMEOUT_CONTEXT_RESUMED == "timeout.context.resumed"
        assert TIMEOUT_UNKNOWN_ACTION_TYPE == "timeout.unknown_action_type"

    def test_parked_context_persistence_events_exist(self) -> None:
        from synthorg.observability.events.persistence import (
            PERSISTENCE_PARKED_CONTEXT_DELETED,
            PERSISTENCE_PARKED_CONTEXT_DESERIALIZE_FAILED,
            PERSISTENCE_PARKED_CONTEXT_NOT_FOUND,
            PERSISTENCE_PARKED_CONTEXT_QUERIED,
            PERSISTENCE_PARKED_CONTEXT_QUERY_FAILED,
            PERSISTENCE_PARKED_CONTEXT_SAVE_FAILED,
            PERSISTENCE_PARKED_CONTEXT_SAVED,
        )

        assert PERSISTENCE_PARKED_CONTEXT_SAVED == "persistence.parked_context.saved"
        assert (
            PERSISTENCE_PARKED_CONTEXT_SAVE_FAILED
            == "persistence.parked_context.save_failed"
        )
        assert (
            PERSISTENCE_PARKED_CONTEXT_QUERIED == "persistence.parked_context.queried"
        )
        assert (
            PERSISTENCE_PARKED_CONTEXT_QUERY_FAILED
            == "persistence.parked_context.query_failed"
        )
        assert (
            PERSISTENCE_PARKED_CONTEXT_NOT_FOUND
            == "persistence.parked_context.not_found"
        )
        assert (
            PERSISTENCE_PARKED_CONTEXT_DELETED == "persistence.parked_context.deleted"
        )
        assert (
            PERSISTENCE_PARKED_CONTEXT_DESERIALIZE_FAILED
            == "persistence.parked_context.deserialize_failed"
        )

    def test_coordination_events_exist(self) -> None:
        from synthorg.observability.events.coordination import (
            COORDINATION_CLEANUP_COMPLETED,
            COORDINATION_CLEANUP_FAILED,
            COORDINATION_CLEANUP_STARTED,
            COORDINATION_COMPLETED,
            COORDINATION_FAILED,
            COORDINATION_PHASE_COMPLETED,
            COORDINATION_PHASE_FAILED,
            COORDINATION_PHASE_STARTED,
            COORDINATION_STARTED,
            COORDINATION_TOPOLOGY_RESOLVED,
            COORDINATION_WAVE_BUILT,
            COORDINATION_WAVE_COMPLETED,
            COORDINATION_WAVE_STARTED,
        )

        assert COORDINATION_STARTED == "coordination.started"
        assert COORDINATION_COMPLETED == "coordination.completed"
        assert COORDINATION_FAILED == "coordination.failed"
        assert COORDINATION_PHASE_STARTED == "coordination.phase.started"
        assert COORDINATION_PHASE_COMPLETED == "coordination.phase.completed"
        assert COORDINATION_PHASE_FAILED == "coordination.phase.failed"
        assert COORDINATION_WAVE_STARTED == "coordination.wave.started"
        assert COORDINATION_WAVE_COMPLETED == "coordination.wave.completed"
        assert COORDINATION_WAVE_BUILT == "coordination.wave.built"
        assert COORDINATION_TOPOLOGY_RESOLVED == "coordination.topology.resolved"
        assert COORDINATION_CLEANUP_STARTED == "coordination.cleanup.started"
        assert COORDINATION_CLEANUP_COMPLETED == "coordination.cleanup.completed"
        assert COORDINATION_CLEANUP_FAILED == "coordination.cleanup.failed"

    def test_classification_events_exist(self) -> None:
        assert CLASSIFICATION_START == "classification.start"
        assert CLASSIFICATION_COMPLETE == "classification.complete"
        assert CLASSIFICATION_FINDING == "classification.finding"
        assert CLASSIFICATION_ERROR == "classification.error"
        assert CLASSIFICATION_SKIPPED == "classification.skipped"
        assert DETECTOR_START == "classification.detector.start"
        assert DETECTOR_COMPLETE == "classification.detector.complete"
        assert DETECTOR_ERROR == "classification.detector.error"
