"""Boot resolves both planning-session spend bounds, in one pair.

The planning session is one of the bounded helper sessions, so it carries a
cost ceiling and a token ceiling. They travel as a ``SessionCeilings`` pair
rather than two scalars precisely so a resolution path cannot carry one and
drop the other, which is the shape that left every flat-rate session with no
bound at all.
"""

import pytest

from synthorg.api.state import AppState
from synthorg.budget.session_budget import SessionCeilings
from synthorg.config.schema import RootConfig
from synthorg.core.types import NotBlankStr
from synthorg.settings import definitions as _definitions  # noqa: F401
from synthorg.settings.enums import SettingNamespace, SettingSource
from synthorg.settings.models import SettingValue
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service_protocol import SettingsServiceProtocol
from synthorg.settings.state import SettingsStateSlice
from synthorg.workers._coordinator_assembly import _resolve_coordinator_dependencies
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit

_COST_KEY = "coordination.decomposition_agent_cost_ceiling"
_TOKEN_KEY = "budget.session_token_ceiling"


def _app_state(overrides: dict[str, str]) -> AppState:
    """State whose settings resolve from *overrides*, else their defaults.

    Returns:
        The composed ``AppState``.
    """

    async def _get(namespace: str, key: str) -> SettingValue:
        definition = get_registry().get(namespace, key)
        default = "" if definition is None else str(definition.default or "")
        return SettingValue(
            namespace=SettingNamespace(namespace),
            key=NotBlankStr(key),
            value=overrides.get(f"{namespace}.{key}", default),
            source=SettingSource.DATABASE,
        )

    config = RootConfig(company_name="test")
    settings_service = mock_of[SettingsServiceProtocol](get=_get)
    app_state = make_app_state(config=config)
    app_state.wire(
        SettingsStateSlice,
        config_resolver=ConfigResolver(
            settings_service=settings_service, config=config
        ),
        settings_service=settings_service,
    )
    return app_state


class TestResolveCoordinatorDependencies:
    async def test_both_ceilings_arrive_as_one_bounded_pair(self) -> None:
        deps = await _resolve_coordinator_dependencies(
            _app_state({_COST_KEY: "2.5", _TOKEN_KEY: "1500000"})
        )

        assert deps.agent_session_ceilings == SessionCeilings(
            cost_ceiling=2.5,
            token_ceiling=1_500_000,
        )
        assert deps.agent_session_ceilings.bounded is True

    async def test_the_token_ceiling_binds_when_money_cannot(self) -> None:
        # The flat-rate case: cost enforcement switched off, and the session
        # is still bounded because the token backstop is counted on every
        # provider.
        deps = await _resolve_coordinator_dependencies(
            _app_state({_COST_KEY: "0", _TOKEN_KEY: "1500000"})
        )

        assert deps.agent_session_ceilings.cost_ceiling == 0.0
        assert deps.agent_session_ceilings.token_ceiling == 1_500_000
        assert deps.agent_session_ceilings.bounded is True

    async def test_opting_out_of_both_is_reported_as_unbounded(self) -> None:
        deps = await _resolve_coordinator_dependencies(
            _app_state({_COST_KEY: "0", _TOKEN_KEY: "0"})
        )

        assert deps.agent_session_ceilings.bounded is False

    async def test_the_rest_of_the_bundle_resolves_alongside_them(self) -> None:
        # The ceilings share a TaskGroup with every other boot read, so a
        # sibling left unresolved would surface as a missing member here
        # rather than as a coordinator built from a half-filled bundle.
        deps = await _resolve_coordinator_dependencies(_app_state({}))

        assert deps.decomposition_strategy
        assert deps.agent_session_max_turns > 0
        assert deps.workspace[0] is not None
        assert deps.planning_memory is not None
