"""Tests for observability event name constants."""

import importlib
import pkgutil
import re

import pytest

from ai_company.observability import events
from ai_company.observability.events.budget import BUDGET_RECORD_ADDED
from ai_company.observability.events.communication import (
    COMM_BUS_ALREADY_RUNNING,
    COMM_BUS_NOT_RUNNING,
    COMM_BUS_STARTED,
    COMM_DISPATCH_NO_DISPATCHER,
    COMM_HANDLER_DEREGISTER_MISS,
    COMM_MESSAGE_PUBLISHED,
)
from ai_company.observability.events.config import (
    CONFIG_LOADED,
    CONFIG_PARSE_FAILED,
    CONFIG_VALIDATION_FAILED,
)
from ai_company.observability.events.conflict import (
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
from ai_company.observability.events.delegation import (
    DELEGATION_CREATED,
    DELEGATION_HIERARCHY_BUILT,
    DELEGATION_HIERARCHY_CYCLE,
    DELEGATION_LOOP_BLOCKED,
    DELEGATION_LOOP_ESCALATED,
    DELEGATION_REQUESTED,
    DELEGATION_RESULT_SENT,
)
from ai_company.observability.events.execution import EXECUTION_TASK_CREATED
from ai_company.observability.events.git import (
    GIT_CLONE_URL_REJECTED,
    GIT_COMMAND_FAILED,
    GIT_COMMAND_START,
    GIT_COMMAND_SUCCESS,
    GIT_COMMAND_TIMEOUT,
    GIT_REF_INJECTION_BLOCKED,
    GIT_WORKSPACE_VIOLATION,
)
from ai_company.observability.events.prompt import PROMPT_BUILD_START
from ai_company.observability.events.provider import (
    PROVIDER_CALL_START,
    PROVIDER_REGISTRY_BUILT,
)
from ai_company.observability.events.role import ROLE_LOOKUP_MISS
from ai_company.observability.events.routing import ROUTING_DECISION_MADE
from ai_company.observability.events.sandbox import (
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
from ai_company.observability.events.task import TASK_STATUS_CHANGED
from ai_company.observability.events.task_assignment import (
    TASK_ASSIGNMENT_AGENT_SCORED,
    TASK_ASSIGNMENT_AGENT_SELECTED,
    TASK_ASSIGNMENT_COMPLETE,
    TASK_ASSIGNMENT_FAILED,
    TASK_ASSIGNMENT_MANUAL_VALIDATED,
    TASK_ASSIGNMENT_NO_ELIGIBLE,
    TASK_ASSIGNMENT_STARTED,
    TASK_ASSIGNMENT_WORKLOAD_BALANCED,
)
from ai_company.observability.events.template import (
    TEMPLATE_RENDER_START,
    TEMPLATE_RENDER_SUCCESS,
)
from ai_company.observability.events.tool import TOOL_INVOKE_START

pytestmark = pytest.mark.timeout(30)

_DOT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


def _all_event_names() -> list[tuple[str, str]]:
    """Return (attr_name, value) for every public string constant."""
    result: list[tuple[str, str]] = []
    for info in pkgutil.iter_modules(events.__path__):
        mod = importlib.import_module(f"ai_company.observability.events.{info.name}")
        for attr in dir(mod):
            if attr.startswith("_"):
                continue
            val = getattr(mod, attr)
            if attr.isupper() and isinstance(val, str):
                result.append((attr, val))
    return result


@pytest.mark.unit
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
            "budget",
            "communication",
            "company",
            "config",
            "conflict",
            "correlation",
            "decomposition",
            "delegation",
            "execution",
            "git",
            "meeting",
            "parallel",
            "personality",
            "prompt",
            "provider",
            "role",
            "routing",
            "sandbox",
            "task",
            "task_assignment",
            "task_routing",
            "template",
            "tool",
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
        from ai_company.observability.events.meeting import (
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
