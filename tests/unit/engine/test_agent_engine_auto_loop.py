"""Unit tests for AgentEngine auto-loop selection integration."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.testing

from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.core.task_enums import Complexity, TaskStatus, TaskType
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_selector import AutoLoopConfig, AutoLoopRule, LoopType
from synthorg.engine.openhands.errors import OpenHandsUnavailableError
from synthorg.engine.quality.classifier import RuleBasedStepClassifier
from synthorg.engine.react_loop import ReactLoop
from synthorg.engine.run_result import AgentRunResult
from synthorg.observability.events.execution import (
    EXECUTION_LOOP_AUTO_SELECTED,
    EXECUTION_LOOP_SELECTION_RESOLVED,
    EXECUTION_LOOP_STATIC_SELECTED,
)
from synthorg.settings.resolver import ConfigResolver
from synthorg.workers._openhands_wiring import build_auto_loop_config_or_none
from tests._shared import as_uuid, make_app_state, mock_of

if TYPE_CHECKING:
    from .conftest import MockCompletionProvider

from .conftest import make_completion_response as _make_completion_response

# ── Helpers ──────────────────────────────────────────────────


def _make_task_with_complexity(
    *,
    complexity: Complexity,
    agent_id: str,
    task_id: str = "task-auto-001",
) -> Task:
    """Build a task with specific complexity for auto-loop tests."""
    return Task(
        id=as_uuid(task_id),
        title="Auto-loop test task",
        description="A task for testing auto-loop selection.",
        type=TaskType.DEVELOPMENT,
        project="proj-001",
        created_by="manager",
        assigned_to=agent_id,
        status=TaskStatus.ASSIGNED,
        estimated_complexity=complexity,
    )


def _loop_settings_app_state(values: dict[str, str]) -> AppState:
    """Build an app state whose resolver reads *values* on every call.

    The dict is read at call time rather than captured, so a test can write a
    new value and re-resolve exactly as an operator's write plus a runtime
    rebuild does.
    """

    async def get_bool(namespace: str, key: str) -> bool:
        return values[f"{namespace}.{key}"] == "true"

    async def get_str(namespace: str, key: str) -> str:
        return values[f"{namespace}.{key}"]

    return make_app_state(
        config=RootConfig(company_name="test-corp"),
        config_resolver=mock_of[ConfigResolver](
            get_bool=AsyncMock(side_effect=get_bool),
            get_str=AsyncMock(side_effect=get_str),
        ),
    )


# ── Live loop selection ──────────────────────────────────────


@pytest.mark.unit
class TestLoopSelectionAppliesLive:
    """An operator's write reaches the next task through a rebuild.

    ``engine.loop_auto_select_enabled`` and its two companions are resolved by
    ``build_auto_loop_config_or_none`` into the frozen ``AutoLoopConfig`` the
    engine then holds, and ``RuntimeReloadSettingsSubscriber`` watches all three
    so a write rebuilds that engine. These tests pin the half of the chain that
    turns a new setting value into a different loop.
    """

    async def test_resolution_is_logged_with_the_gate_off(self) -> None:
        # The rebuild is the only moment these keys are read, so the resolved
        # verdict is the sole operator-visible evidence that a write landed.
        # Logging only the enabled branch would leave a disabled loop silent
        # and indistinguishable from a rebuild that never ran.
        app_state = _loop_settings_app_state(
            {
                "engine.loop_auto_select_enabled": "false",
                "engine.default_loop_type": "react",
                "engine.loop_complexity_overrides": "",
            }
        )
        with structlog.testing.capture_logs() as logs:
            assert await build_auto_loop_config_or_none(app_state) is None

        resolved = [
            e for e in logs if e.get("event") == EXECUTION_LOOP_SELECTION_RESOLVED
        ]
        assert len(resolved) == 1
        assert resolved[0]["enabled"] is False

    async def test_resolution_is_logged_with_the_gate_on(self) -> None:
        app_state = _loop_settings_app_state(
            {
                "engine.loop_auto_select_enabled": "true",
                "engine.default_loop_type": "openhands",
                "engine.loop_complexity_overrides": "",
            }
        )
        with structlog.testing.capture_logs() as logs:
            config = await build_auto_loop_config_or_none(app_state)

        assert config is not None
        resolved = [
            e for e in logs if e.get("event") == EXECUTION_LOOP_SELECTION_RESOLVED
        ]
        assert len(resolved) == 1
        # The logged verdict has to match the config actually built, or the
        # operator is reading a value the engine is not using.
        assert resolved[0]["enabled"] is True
        assert resolved[0]["default_loop_type"] == config.default_loop_type
        assert resolved[0]["rule_count"] == len(config.rules)

    async def test_flipping_the_gate_switches_from_static_to_selected(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        values = {
            "engine.loop_auto_select_enabled": "false",
            "engine.default_loop_type": "react",
            "engine.loop_complexity_overrides": "medium:openhands",
        }
        app_state = _loop_settings_app_state(values)
        task = _make_task_with_complexity(
            complexity=Complexity.MEDIUM,
            agent_id="agent-live-001",
            task_id="task-live-001",
        )

        # Threaded through rather than asserted and discarded, so the chain
        # under test is the one the rebuild actually performs: gate off, no
        # config, static loop.
        before = AgentEngine(
            provider=mock_provider_factory([]),
            auto_loop_config=await build_auto_loop_config_or_none(app_state),
        )
        assert before._auto_loop_config is None
        assert isinstance(
            await before._resolve_loop(task, "agent-live-001", str(task.id)),
            ReactLoop,
        )

        values["engine.loop_auto_select_enabled"] = "true"
        config = await build_auto_loop_config_or_none(app_state)
        assert config is not None
        after = AgentEngine(
            provider=mock_provider_factory([]),
            auto_loop_config=config,
        )
        # The override now routes MEDIUM at openhands, which is unwired here
        # and says so rather than quietly running react.
        with pytest.raises(OpenHandsUnavailableError):
            await after._resolve_loop(task, "agent-live-001", str(task.id))

    async def test_an_override_write_changes_the_loop_a_complexity_gets(
        self,
    ) -> None:
        values = {
            "engine.loop_auto_select_enabled": "true",
            "engine.default_loop_type": "react",
            "engine.loop_complexity_overrides": "",
        }
        app_state = _loop_settings_app_state(values)

        default_config = await build_auto_loop_config_or_none(app_state)
        assert default_config is not None
        assert default_config.rules == AutoLoopConfig().rules

        values["engine.loop_complexity_overrides"] = "medium:openhands"
        overridden_config = await build_auto_loop_config_or_none(app_state)
        assert overridden_config is not None
        by_complexity = {r.complexity: r.loop_type for r in overridden_config.rules}
        assert by_complexity[Complexity.MEDIUM] == "openhands"
        assert by_complexity[Complexity.SIMPLE] == "react"


# ── Retired stored values ────────────────────────────────────


@pytest.mark.unit
class TestRetiredStoredLoopValues:
    """A row naming a deleted loop resolves rather than breaking the rebuild.

    A setting is validated on write and never on read, so a value stored while
    plan_execute / hybrid were valid reaches ``AutoLoopConfig`` unchanged. The
    data migration rewrites the stored rows, but an env-supplied value is
    beyond its reach, so the read boundary maps the name too.
    """

    @pytest.mark.parametrize("retired", ["plan_execute", "hybrid"])
    async def test_a_stored_default_loop_type_resolves_to_react(
        self, retired: str
    ) -> None:
        app_state = _loop_settings_app_state(
            {
                "engine.loop_auto_select_enabled": "true",
                "engine.default_loop_type": retired,
                "engine.loop_complexity_overrides": "",
            }
        )
        config = await build_auto_loop_config_or_none(app_state)
        assert config is not None
        assert config.default_loop_type == "react"

    async def test_stored_overrides_naming_retired_loops_resolve_to_react(
        self,
    ) -> None:
        app_state = _loop_settings_app_state(
            {
                "engine.loop_auto_select_enabled": "true",
                "engine.default_loop_type": "react",
                "engine.loop_complexity_overrides": (
                    "medium:hybrid,complex:plan_execute"
                ),
            }
        )
        config = await build_auto_loop_config_or_none(app_state)
        assert config is not None
        by_complexity = {r.complexity: r.loop_type for r in config.rules}
        assert by_complexity[Complexity.MEDIUM] == "react"
        assert by_complexity[Complexity.COMPLEX] == "react"

    async def test_a_retired_value_does_not_shadow_a_live_one(self) -> None:
        """Only the retired name is rewritten; openhands still routes."""
        app_state = _loop_settings_app_state(
            {
                "engine.loop_auto_select_enabled": "true",
                "engine.default_loop_type": "react",
                "engine.loop_complexity_overrides": ("medium:hybrid,epic:openhands"),
            }
        )
        config = await build_auto_loop_config_or_none(app_state)
        assert config is not None
        by_complexity = {r.complexity: r.loop_type for r in config.rules}
        assert by_complexity[Complexity.MEDIUM] == "react"
        assert by_complexity[Complexity.EPIC] == "openhands"


# ── Auto-loop selection ──────────────────────────────────────


@pytest.mark.unit
class TestAutoLoopSelection:
    """AgentEngine with auto_loop_config selects loop per task complexity."""

    @pytest.mark.parametrize(
        "complexity",
        [
            Complexity.SIMPLE,
            Complexity.MEDIUM,
            Complexity.COMPLEX,
            Complexity.EPIC,
        ],
    )
    async def test_every_complexity_defaults_to_react(
        self,
        complexity: Complexity,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(
            provider=provider,
            auto_loop_config=AutoLoopConfig(),
        )
        task = _make_task_with_complexity(
            complexity=complexity,
            agent_id=str(sample_agent_with_personality.id),
        )

        with structlog.testing.capture_logs() as logs:
            result = await engine.run(
                identity=sample_agent_with_personality,
                task=task,
            )

        assert isinstance(result, AgentRunResult)
        selected_events = [
            e for e in logs if e.get("event") == EXECUTION_LOOP_AUTO_SELECTED
        ]
        assert len(selected_events) == 1
        assert selected_events[0]["selected_loop"] == "react"

    async def test_an_override_rule_routes_that_complexity_elsewhere(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """A measured override reaches the builder, unwired deps and all."""
        engine = AgentEngine(
            provider=mock_provider_factory([]),
            auto_loop_config=AutoLoopConfig(
                rules=(
                    AutoLoopRule(
                        complexity=Complexity.EPIC,
                        loop_type=LoopType.OPENHANDS,
                    ),
                ),
            ),
        )
        task = _make_task_with_complexity(
            complexity=Complexity.EPIC,
            agent_id="agent-auto-oh",
            task_id="task-auto-oh",
        )
        with pytest.raises(OpenHandsUnavailableError):
            await engine._resolve_loop(task, "agent-auto-oh", str(task.id))

    async def test_static_loop_emits_static_selected_event(
        self,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Without auto-selection, the static-loop path records its choice."""
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(provider=provider)
        task = _make_task_with_complexity(
            complexity=Complexity.SIMPLE,
            agent_id=str(sample_agent_with_personality.id),
        )

        with structlog.testing.capture_logs() as logs:
            result = await engine.run(
                identity=sample_agent_with_personality,
                task=task,
            )

        assert isinstance(result, AgentRunResult)
        static_events = [
            e for e in logs if e.get("event") == EXECUTION_LOOP_STATIC_SELECTED
        ]
        assert len(static_events) == 1
        assert static_events[0]["loop_type"] == "react"
        assert not [e for e in logs if e.get("event") == EXECUTION_LOOP_AUTO_SELECTED]


# ── Mutual exclusivity ──────────────────────────────────────


@pytest.mark.unit
class TestAutoLoopWithExplicitLoop:
    """execution_loop and auto_loop_config are mutually exclusive."""

    def test_both_raises_value_error(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        provider = mock_provider_factory([])
        with pytest.raises(ValueError, match="mutually exclusive"):
            AgentEngine(
                provider=provider,
                execution_loop=ReactLoop(),
                auto_loop_config=AutoLoopConfig(),
            )


# -- Resume path with auto-loop -----------------------------------


@pytest.mark.unit
class TestAutoLoopResumePath:
    """Resume path calls _resolve_loop, not static self._loop."""

    async def test_execute_resumed_loop_calls_resolve_loop(
        self,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """_execute_resumed_loop delegates to _resolve_loop for auto mode."""
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(
            provider=provider,
            auto_loop_config=AutoLoopConfig(),
        )

        task = _make_task_with_complexity(
            complexity=Complexity.MEDIUM,
            agent_id=str(sample_agent_with_personality.id),
        )
        checkpoint_ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=task,
        )

        # Build a mock loop whose execute we can assert on.
        exec_result = MagicMock()
        exec_result.termination_reason = MagicMock()
        exec_result.termination_reason.value = "completed"

        resolved_loop = MagicMock()
        resolved_loop.execute = AsyncMock(return_value=exec_result)
        resolve_mock = AsyncMock(spec=engine._resolve_loop, return_value=resolved_loop)

        with patch.object(engine, "_resolve_loop", resolve_mock):
            await engine._execute_resumed_loop(
                checkpoint_ctx,
                str(sample_agent_with_personality.id),
                str(task.id),
            )

        # _resolve_loop was called with the checkpoint's task + IDs
        resolve_mock.assert_awaited_once()
        call_args = resolve_mock.call_args
        call_task = call_args[0][0]
        assert call_task.estimated_complexity == Complexity.MEDIUM
        assert call_args[0][1] == str(sample_agent_with_personality.id)
        assert call_args[0][2] == str(task.id)

        # The resolved loop instance was actually executed
        resolved_loop.execute.assert_awaited_once()


# -- Config wiring through auto-selection path -------------------


@pytest.mark.unit
class TestAutoLoopConfigWiring:
    """The engine's collaborators reach the auto-selected loop."""

    async def test_compaction_callback_wired_via_auto_selection(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        provider = mock_provider_factory([])
        compact_cb = AsyncMock()
        engine = AgentEngine(
            provider=provider,
            auto_loop_config=AutoLoopConfig(),
            compaction_callback=compact_cb,
        )
        task = _make_task_with_complexity(
            complexity=Complexity.SIMPLE,
            agent_id="agent-wire-001",
            task_id="task-wire-001",
        )
        loop = await engine._resolve_loop(task, "agent-wire-001", str(task.id))
        assert isinstance(loop, ReactLoop)
        assert loop.compaction_callback is compact_cb

    async def test_step_classifier_wired_via_auto_selection(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        provider = mock_provider_factory([])
        classifier = RuleBasedStepClassifier()
        engine = AgentEngine(
            provider=provider,
            auto_loop_config=AutoLoopConfig(),
            step_classifier=classifier,
        )
        task = _make_task_with_complexity(
            complexity=Complexity.SIMPLE,
            agent_id="agent-clf-react",
            task_id="task-clf-react",
        )
        loop = await engine._resolve_loop(task, "agent-clf-react", str(task.id))
        assert isinstance(loop, ReactLoop)
        assert loop._step_classifier is classifier

    def test_step_classifier_wired_to_default_loop(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Without auto_loop_config, the default ReactLoop receives it."""
        provider = mock_provider_factory([])
        classifier = RuleBasedStepClassifier()
        engine = AgentEngine(provider=provider, step_classifier=classifier)
        assert isinstance(engine._loop, ReactLoop)
        assert engine._loop._step_classifier is classifier

    def test_compaction_callback_wired_to_default_loop(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Without auto_loop_config, default ReactLoop receives callback."""
        provider = mock_provider_factory([])
        compact_cb = MagicMock()
        engine = AgentEngine(
            provider=provider,
            compaction_callback=compact_cb,
        )
        assert isinstance(engine._loop, ReactLoop)
        assert engine._loop.compaction_callback is compact_cb

    def test_compaction_callback_defaults_to_none(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Omitting compaction_callback leaves loop attribute None."""
        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider)
        assert isinstance(engine._loop, ReactLoop)
        assert engine._loop.compaction_callback is None
