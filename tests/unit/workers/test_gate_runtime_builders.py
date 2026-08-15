"""What arms each completion gate at worker boot.

Both gates are armed by their own enabled flag and nothing else: the judge
is a roster agent selected per evaluation, running on the pair an operator
bound to it, so there is no model to resolve here and no way for an unset
setting to leave a gate quietly unarmed. That is precisely why these
builders need tests: a regression returning ``None`` disarms a shipped
org's gate with no verdict, no park and no log to say it happened.
"""

import pytest

from synthorg.config.schema import RootConfig
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.completion_oracle.builder import (
    build_completion_oracle_tool_seed,
)
from synthorg.engine.completion_oracle.config import CompletionOracleConfig
from synthorg.engine.routing_policy import CapabilityPolicy
from synthorg.hr.registry import AgentRegistryService
from synthorg.security.config import SecurityConfig
from synthorg.security.config._components import RedTeamConfig
from synthorg.security.redteam.builder import build_red_team_tool_seed
from synthorg.workers._completion_oracle_runtime import (
    build_completion_oracle_runtime_or_none,
)
from synthorg.workers._red_team_runtime import build_red_team_runtime_or_none
from tests._shared import make_app_state
from tests._shared.staffing import roster_capability_policy
from tests.unit.engine.conftest import MockCompletionProvider

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _capability_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for the process-wide capability policy both builders read.

    Building the real one needs a provider catalogue; what these tests are
    about is whether the enabled flag alone arms the gate.
    """

    async def _build(_state: object) -> CapabilityPolicy | None:
        return roster_capability_policy()

    for module in (
        "synthorg.workers._completion_oracle_runtime",
        "synthorg.workers._red_team_runtime",
    ):
        monkeypatch.setattr(f"{module}.build_capability_policy", _build)


def _engine() -> AgentEngine:
    """Build an engine the runtime can hang its gate off.

    Returns:
        An ``AgentEngine`` over a provider that is never called here.
    """
    return AgentEngine(provider=MockCompletionProvider([]))


class TestCompletionOracleRuntime:
    async def test_the_enabled_oracle_is_armed_without_any_model_setting(self) -> None:
        """No setting stands between "enabled" and "armed".

        The reviewer names its own pair, so a runtime that came back
        ``None`` here would be a gate an operator turned on and never got.
        """
        config = CompletionOracleConfig(enabled=True)
        state = make_app_state(agent_registry=AgentRegistryService())

        runtime = await build_completion_oracle_runtime_or_none(
            app_state=state,
            engine=_engine(),
            seed=build_completion_oracle_tool_seed(config=config),
            config=config,
        )

        assert runtime is not None
        assert runtime.gate is not None

    async def test_a_disabled_oracle_builds_nothing(self) -> None:
        config = CompletionOracleConfig(enabled=False)
        state = make_app_state(agent_registry=AgentRegistryService())

        runtime = await build_completion_oracle_runtime_or_none(
            app_state=state,
            engine=_engine(),
            seed=build_completion_oracle_tool_seed(config=config),
            config=config,
        )

        assert runtime is None

    async def test_an_empty_roster_still_arms_the_gate(self) -> None:
        """Unstaffed is a per-review verdict, never a silent boot-time skip.

        A gate that declined to build because nobody holds the role would
        pass every deliverable unreviewed until somebody restarted the
        process. It builds, and each review parks instead.
        """
        config = CompletionOracleConfig(enabled=True)
        registry = AgentRegistryService()
        state = make_app_state(agent_registry=registry)

        runtime = await build_completion_oracle_runtime_or_none(
            app_state=state,
            engine=_engine(),
            seed=build_completion_oracle_tool_seed(config=config),
            config=config,
        )

        assert runtime is not None


class TestRedTeamRuntime:
    async def test_the_enabled_gate_is_armed_without_any_adversary_model(self) -> None:
        config = RedTeamConfig(enabled=True)
        state = make_app_state(
            config=RootConfig(
                company_name="test", security=SecurityConfig(red_team=config)
            ),
            agent_registry=AgentRegistryService(),
        )

        runtime = await build_red_team_runtime_or_none(
            app_state=state,
            engine=_engine(),
            seed=build_red_team_tool_seed(config=config),
        )

        assert runtime is not None
        assert runtime.gate is not None

    async def test_a_disabled_gate_builds_nothing(self) -> None:
        config = RedTeamConfig(enabled=False)
        state = make_app_state(
            config=RootConfig(
                company_name="test", security=SecurityConfig(red_team=config)
            ),
            agent_registry=AgentRegistryService(),
        )

        runtime = await build_red_team_runtime_or_none(
            app_state=state,
            engine=_engine(),
            seed=build_red_team_tool_seed(config=config),
        )

        assert runtime is None
