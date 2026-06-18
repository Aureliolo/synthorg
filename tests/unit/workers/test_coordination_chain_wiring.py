"""_build_coordination_chain gates and composes the coordination pipeline."""

from types import SimpleNamespace
from typing import cast

import pytest

from synthorg.api.state import AppState
from synthorg.core.middleware_config import DEFAULT_COORDINATION_CHAIN
from synthorg.engine.coordination.section_config import CoordinationSectionConfig
from synthorg.engine.middleware.coordination_constraints import (
    MagenticReplanHook,
    NoOpReplanHook,
    ReplanMiddleware,
)
from synthorg.workers._coordinator_assembly import _build_coordination_chain

pytestmark = pytest.mark.unit


def _app_state(section: CoordinationSectionConfig) -> AppState:
    fake = SimpleNamespace(
        config=SimpleNamespace(coordination=section),
        slice=lambda _slice_type: SimpleNamespace(budget_enforcer=None),
    )
    return cast("AppState", fake)


def _replan_hook(chain: object) -> object:
    middleware = next(
        mw
        for mw in chain.middleware  # type: ignore[attr-defined]
        if isinstance(mw, ReplanMiddleware)
    )
    return middleware._hook


class TestBuildCoordinationChain:
    def test_disabled_returns_none(self) -> None:
        section = CoordinationSectionConfig(enable_coordination_middleware=False)
        assert _build_coordination_chain(_app_state(section)) is None

    def test_enabled_builds_full_default_chain(self) -> None:
        section = CoordinationSectionConfig(
            enable_coordination_middleware=True,
            decomposition_model="example-medium-001",
        )
        chain = _build_coordination_chain(_app_state(section))
        assert chain is not None
        assert chain.names == DEFAULT_COORDINATION_CHAIN

    def test_noop_replan_is_safe_default(self) -> None:
        section = CoordinationSectionConfig(
            enable_coordination_middleware=True,
            decomposition_model="example-medium-001",
        )
        chain = _build_coordination_chain(_app_state(section))
        assert chain is not None
        assert isinstance(_replan_hook(chain), NoOpReplanHook)

    def test_magentic_replan_opt_in(self) -> None:
        section = CoordinationSectionConfig(
            enable_coordination_middleware=True,
            replan_strategy="magentic",
            decomposition_model="example-medium-001",
        )
        chain = _build_coordination_chain(_app_state(section))
        assert chain is not None
        assert isinstance(_replan_hook(chain), MagenticReplanHook)

    def test_enabled_without_decomposition_model_raises(self) -> None:
        section = CoordinationSectionConfig(
            enable_coordination_middleware=True,
            decomposition_model="",
        )
        with pytest.raises(ValueError, match="decomposition_model must be set"):
            _build_coordination_chain(_app_state(section))
