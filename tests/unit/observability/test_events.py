"""Tests for observability event name constants."""

import importlib
import pkgutil
import re

import pytest

from ai_company.observability import events
from ai_company.observability.events.budget import BUDGET_RECORD_ADDED
from ai_company.observability.events.communication import (
    COMM_BUS_STARTED,
    COMM_MESSAGE_PUBLISHED,
)
from ai_company.observability.events.config import (
    CONFIG_LOADED,
    CONFIG_PARSE_FAILED,
    CONFIG_VALIDATION_FAILED,
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
            "config",
            "correlation",
            "execution",
            "git",
            "prompt",
            "provider",
            "role",
            "routing",
            "sandbox",
            "task",
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
        assert COMM_MESSAGE_PUBLISHED == "communication.message.published"

    def test_tool_events_exist(self) -> None:
        assert TOOL_INVOKE_START == "tool.invoke.start"
