"""Unit tests for :class:`CeremonyPolicyService`.

The service's ``get_policy`` / ``get_resolved_policy`` paths re-use
the controller helpers, so we patch those in-place rather than
re-mocking every transitive dep (settings + config resolver + ...).
The ``get_active_strategy`` path hits the ceremony scheduler
directly -- easier to test with a minimal stub.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from synthorg.coordination.ceremony_policy.service import (
    ActiveCeremonyStrategy,
    CeremonyPolicyService,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.ceremony_policy import (
    CeremonyPolicyConfig,
    CeremonyStrategyType,
)

if TYPE_CHECKING:
    from synthorg.api.controllers.ceremony_policy import (
        ResolvedCeremonyPolicyResponse,
    )
    from synthorg.api.state import AppState

pytestmark = pytest.mark.unit


@dataclass
class _SchedulerStrategy:
    strategy_type: CeremonyStrategyType


@dataclass
class _SchedulerSprint:
    id: str


class _FakeScheduler:
    def __init__(
        self,
        *,
        running: bool,
        strategy: CeremonyStrategyType | None = None,
        sprint_id: str | None = None,
    ) -> None:
        self.running = running
        self._strategy = (
            _SchedulerStrategy(strategy_type=strategy) if strategy is not None else None
        )
        self._sprint = _SchedulerSprint(id=sprint_id) if sprint_id is not None else None

    async def get_active_info(self):  # type: ignore[no-untyped-def]
        return self._strategy, self._sprint


class _FakeAppState:
    def __init__(
        self,
        *,
        scheduler: _FakeScheduler | None = None,
    ) -> None:
        self.ceremony_scheduler = scheduler


class TestGetPolicy:
    async def test_delegates_to_controller_helper(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        expected = CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.TASK_DRIVEN,
        )

        async def _fake_fetch(_state: AppState) -> CeremonyPolicyConfig:
            return expected

        monkeypatch.setattr(
            "synthorg.api.controllers.ceremony_policy._fetch_project_policy",
            _fake_fetch,
        )
        service = CeremonyPolicyService(
            app_state=_FakeAppState(),  # type: ignore[arg-type]
        )

        result = await service.get_policy()

        assert result is expected


class TestGetResolvedPolicy:
    async def test_no_department(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from synthorg.api.controllers.ceremony_policy import (
            PolicyFieldOrigin,
            ResolvedCeremonyPolicyResponse,
            ResolvedPolicyField,
        )

        project = CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.HYBRID,
        )
        response = ResolvedCeremonyPolicyResponse(
            strategy=ResolvedPolicyField(
                value=CeremonyStrategyType.HYBRID.value,
                source=PolicyFieldOrigin.PROJECT,
            ),
            strategy_config=ResolvedPolicyField(
                value={},
                source=PolicyFieldOrigin.DEFAULT,
            ),
            velocity_calculator=ResolvedPolicyField(
                value="multi_dimensional",
                source=PolicyFieldOrigin.DEFAULT,
            ),
            auto_transition=ResolvedPolicyField(
                value=True,
                source=PolicyFieldOrigin.DEFAULT,
            ),
            transition_threshold=ResolvedPolicyField(
                value=1.0,
                source=PolicyFieldOrigin.DEFAULT,
            ),
        )

        async def _fake_fetch(_state: AppState) -> CeremonyPolicyConfig:
            return project

        def _fake_build(
            proj: CeremonyPolicyConfig,
            dept: CeremonyPolicyConfig | None,
        ) -> ResolvedCeremonyPolicyResponse:
            assert proj is project
            assert dept is None
            return response

        monkeypatch.setattr(
            "synthorg.api.controllers.ceremony_policy._fetch_project_policy",
            _fake_fetch,
        )
        monkeypatch.setattr(
            "synthorg.api.controllers.ceremony_policy._build_resolved_response",
            _fake_build,
        )
        service = CeremonyPolicyService(
            app_state=_FakeAppState(),  # type: ignore[arg-type]
        )

        result = await service.get_resolved_policy()

        assert result is response

    async def test_with_department(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from synthorg.api.controllers.ceremony_policy import (
            PolicyFieldOrigin,
            ResolvedCeremonyPolicyResponse,
            ResolvedPolicyField,
        )

        project = CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.TASK_DRIVEN,
        )
        dept = CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.HYBRID,
        )
        calls: dict[str, object] = {}
        response = ResolvedCeremonyPolicyResponse(
            strategy=ResolvedPolicyField(
                value=CeremonyStrategyType.HYBRID.value,
                source=PolicyFieldOrigin.DEPARTMENT,
            ),
            strategy_config=ResolvedPolicyField(
                value={},
                source=PolicyFieldOrigin.DEFAULT,
            ),
            velocity_calculator=ResolvedPolicyField(
                value="multi_dimensional",
                source=PolicyFieldOrigin.DEFAULT,
            ),
            auto_transition=ResolvedPolicyField(
                value=True,
                source=PolicyFieldOrigin.DEFAULT,
            ),
            transition_threshold=ResolvedPolicyField(
                value=1.0,
                source=PolicyFieldOrigin.DEFAULT,
            ),
        )

        async def _fake_project(_state: AppState) -> CeremonyPolicyConfig:
            return project

        async def _fake_dept(
            _state: AppState,
            department: NotBlankStr,
        ) -> CeremonyPolicyConfig:
            calls["department"] = department
            return dept

        def _fake_build(
            proj: CeremonyPolicyConfig,
            d: CeremonyPolicyConfig | None,
        ) -> ResolvedCeremonyPolicyResponse:
            calls["proj"] = proj
            calls["dept"] = d
            return response

        monkeypatch.setattr(
            "synthorg.api.controllers.ceremony_policy._fetch_project_policy",
            _fake_project,
        )
        monkeypatch.setattr(
            "synthorg.api.controllers.ceremony_policy._fetch_department_policy",
            _fake_dept,
        )
        monkeypatch.setattr(
            "synthorg.api.controllers.ceremony_policy._build_resolved_response",
            _fake_build,
        )
        service = CeremonyPolicyService(
            app_state=_FakeAppState(),  # type: ignore[arg-type]
        )

        result = await service.get_resolved_policy(
            department=NotBlankStr("engineering"),
        )

        assert result is response
        assert calls["department"] == "engineering"
        assert calls["proj"] is project
        assert calls["dept"] is dept


class TestGetResolvedPolicyDepartmentNotFound:
    """NotFoundError on the department lookup must propagate unchanged.

    The handler layer relies on ``NotFoundError`` to map to the
    shared ``not_found`` wire envelope. If the service caught the
    error and re-raised something else, MCP clients would see a
    generic ``internal_error`` for a user-correctable bad department
    name.
    """

    async def test_not_found_propagates(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from synthorg.core.domain_errors import NotFoundError

        project = CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.TASK_DRIVEN,
        )

        async def _fake_project(_state: AppState) -> CeremonyPolicyConfig:
            return project

        async def _fake_dept(
            _state: AppState,
            department: NotBlankStr,
        ) -> CeremonyPolicyConfig:
            msg = f"department {department!r} not found"
            raise NotFoundError(msg)

        monkeypatch.setattr(
            "synthorg.api.controllers.ceremony_policy._fetch_project_policy",
            _fake_project,
        )
        monkeypatch.setattr(
            "synthorg.api.controllers.ceremony_policy._fetch_department_policy",
            _fake_dept,
        )
        service = CeremonyPolicyService(
            app_state=_FakeAppState(),  # type: ignore[arg-type]
        )

        with pytest.raises(NotFoundError, match="not found"):
            await service.get_resolved_policy(
                department=NotBlankStr("nonexistent"),
            )


class TestGetActiveStrategy:
    async def test_no_scheduler(self) -> None:
        service = CeremonyPolicyService(
            app_state=_FakeAppState(scheduler=None),  # type: ignore[arg-type]
        )

        result = await service.get_active_strategy()

        assert isinstance(result, ActiveCeremonyStrategy)
        assert result.strategy is None
        assert result.sprint_id is None

    async def test_not_running(self) -> None:
        scheduler = _FakeScheduler(running=False)
        service = CeremonyPolicyService(
            app_state=_FakeAppState(scheduler=scheduler),  # type: ignore[arg-type]
        )

        result = await service.get_active_strategy()

        assert result.strategy is None
        assert result.sprint_id is None

    async def test_running_returns_strategy_and_sprint(self) -> None:
        scheduler = _FakeScheduler(
            running=True,
            strategy=CeremonyStrategyType.HYBRID,
            sprint_id="sprint-42",
        )
        service = CeremonyPolicyService(
            app_state=_FakeAppState(scheduler=scheduler),  # type: ignore[arg-type]
        )

        result = await service.get_active_strategy()

        assert result.strategy == CeremonyStrategyType.HYBRID
        assert result.sprint_id == "sprint-42"

    async def test_running_but_no_active_info(self) -> None:
        scheduler = _FakeScheduler(
            running=True,
            strategy=None,
            sprint_id=None,
        )
        service = CeremonyPolicyService(
            app_state=_FakeAppState(scheduler=scheduler),  # type: ignore[arg-type]
        )

        result = await service.get_active_strategy()

        assert result.strategy is None
        assert result.sprint_id is None
