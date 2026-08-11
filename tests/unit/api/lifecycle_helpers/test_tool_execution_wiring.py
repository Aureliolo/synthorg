"""The tool plane publishes what it can do, or declines naming what it cannot.

``GET /subsystems`` exists to answer "why is this not up". For this subsystem
the condition is the platform rather than a blank setting, so the activation
has to carry the reason itself; a decline that named nothing would land in the
"declined on a condition it does not declare" fallback the reason mechanism was
built to remove.

The other half these tests pin is that a decline installs NOTHING. Liveness is
read from the published report, so an activation that both declined and left a
report behind would read `active` while the tools it reports on cannot run.
"""

from pathlib import Path

import pytest

from synthorg.api.lifecycle_helpers.tool_execution_wiring import (
    wire_tool_execution_capability,
)
from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.engine.workspace.state import WorkspaceStateSlice
from synthorg.tools.sandbox.execution_capability import (
    ProbeOutcome,
    ToolExecutionCapability,
)
from synthorg.tools.sandbox.workspace_mount import WorkspaceMount
from synthorg.tools.state import ToolsStateSlice
from tests._shared import make_app_state

pytestmark = pytest.mark.unit

_NO_SPAWN = "this process's event loop cannot spawn a subprocess, so git dies"
_NO_DAEMON = "the Docker daemon is unreachable, so no CodeExecutionRecord"


def _app_state(tmp_path: Path) -> AppState:
    app_state = make_app_state()
    app_state.wire(WorkspaceStateSlice, agent_workspace_root=tmp_path)
    return app_state


def _reports(
    capability: ToolExecutionCapability,
    monkeypatch: pytest.MonkeyPatch,
) -> list[Path]:
    """Make the probe answer *capability*, recording the workspace it got."""
    seen: list[Path] = []

    async def _probe(*, workspace: Path, **kwargs: object) -> ToolExecutionCapability:
        del kwargs
        seen.append(workspace)
        return capability

    monkeypatch.setattr(
        "synthorg.api.lifecycle_helpers.tool_execution_wiring.probe_tool_execution",
        _probe,
    )
    return seen


class TestActivation:
    async def test_a_working_plane_publishes_its_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app_state = _app_state(tmp_path)
        capability = ToolExecutionCapability(
            subprocess=ProbeOutcome(available=True),
            container=ProbeOutcome(available=True),
            workspace_mount=WorkspaceMount(volume="vol", subpath="agent-workspaces"),
        )
        seen = _reports(capability, monkeypatch)

        await wire_tool_execution_capability(app_state)

        assert app_state.slice(ToolsStateSlice).tool_execution is capability
        assert seen == [tmp_path]

    async def test_a_dead_loop_declines_with_its_own_reason(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app_state = _app_state(tmp_path)
        _reports(
            ToolExecutionCapability(
                subprocess=ProbeOutcome(available=False, reason=_NO_SPAWN),
                container=ProbeOutcome(available=True),
            ),
            monkeypatch,
        )

        with pytest.raises(SubsystemDeclinedError, match="event loop"):
            await wire_tool_execution_capability(app_state)

    async def test_an_unreachable_daemon_declines_with_its_own_reason(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app_state = _app_state(tmp_path)
        _reports(
            ToolExecutionCapability(
                subprocess=ProbeOutcome(available=True),
                container=ProbeOutcome(available=False, reason=_NO_DAEMON),
            ),
            monkeypatch,
        )

        with pytest.raises(SubsystemDeclinedError, match="CodeExecutionRecord"):
            await wire_tool_execution_capability(app_state)

    async def test_a_decline_publishes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app_state = _app_state(tmp_path)
        _reports(
            ToolExecutionCapability(
                subprocess=ProbeOutcome(available=False, reason=_NO_SPAWN),
                container=ProbeOutcome(available=False, reason=_NO_DAEMON),
            ),
            monkeypatch,
        )

        with pytest.raises(SubsystemDeclinedError):
            await wire_tool_execution_capability(app_state)

        assert app_state.slice(ToolsStateSlice).tool_execution is None

    async def test_a_probe_that_starts_passing_brings_it_up(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The level-triggered promise: an operator who starts Docker after the
        # backend gets the subsystem on the next sweep, not on a restart.
        app_state = _app_state(tmp_path)
        _reports(
            ToolExecutionCapability(
                subprocess=ProbeOutcome(available=True),
                container=ProbeOutcome(available=False, reason=_NO_DAEMON),
            ),
            monkeypatch,
        )
        with pytest.raises(SubsystemDeclinedError):
            await wire_tool_execution_capability(app_state)

        working = ToolExecutionCapability(
            subprocess=ProbeOutcome(available=True),
            container=ProbeOutcome(available=True),
        )
        _reports(working, monkeypatch)
        await wire_tool_execution_capability(app_state)

        assert app_state.slice(ToolsStateSlice).tool_execution is working
