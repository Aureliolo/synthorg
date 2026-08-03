# module-kind: tests
"""The recorder's own entry point: what it wires, and what it refuses to do.

``_build_deps`` has no behaviour of its own, which is exactly why it needs a
test. Every field it binds is optional on :class:`LoopAbDeps`, so dropping one
type-checks and leaves every other test green while changing what the scoreboard
measures: without ``open_cell_ledger`` the engine's own tracker becomes the
ledger, and the OpenHands leg's spend (recorded only by the gateway, because its
calls happen inside the container) silently disappears again.

The plan path is covered here too. It is the default, so it is what anyone runs
first, and its whole promise is that it costs nothing.
"""

from pathlib import Path

import pytest
from scripts.record_loop_ab import (
    _build_deps,
    _build_tool_registry,
    _parse_args,
    main,
)

from evals.loop_ab.binding import CellBinder
from evals.loop_ab.host import LoopAbGatewayHost
from evals.loop_ab.workspace import CellWorkspace
from evals.runner.execution import EVAL_TASK_PROJECT
from synthorg.tools.file_system import BaseFileSystemTool
from synthorg.tools.sandbox.docker_sandbox import DockerSandbox
from tests.evals_spine.loop_ab.conftest import RECORDING_PROVIDER

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


class TestDepsWiring:
    def test_every_collaborator_is_bound_to_the_host(
        self, host: LoopAbGatewayHost
    ) -> None:
        deps = _build_deps(host)

        # Bound at all: a dropped line here is invisible to the type checker
        # because these two fields default to None.
        assert deps.build_openhands_cell is not None
        assert deps.open_cell_ledger is not None
        assert deps.build_tool_registry is _build_tool_registry

    def test_the_bound_methods_come_from_one_binder_over_this_host(
        self, host: LoopAbGatewayHost
    ) -> None:
        # Bound to the RIGHT thing: three methods of one binder over the started
        # host, not a binder over some other config, and not swapped with each
        # other (which would type-check, since two of them take a CellRun).
        deps = _build_deps(host)
        assert deps.build_openhands_cell is not None
        assert deps.open_cell_ledger is not None

        binder = deps.build_provider.__self__  # type: ignore[attr-defined]

        assert isinstance(binder, CellBinder)
        assert binder.host is host
        assert deps.build_provider.__func__ is CellBinder.build_provider  # type: ignore[attr-defined]
        assert deps.build_openhands_cell.__func__ is CellBinder.build_openhands_cell  # type: ignore[attr-defined]
        assert deps.open_cell_ledger.__func__ is CellBinder.open_cell_ledger  # type: ignore[attr-defined]
        assert deps.build_openhands_cell.__self__ is binder  # type: ignore[attr-defined]
        assert deps.open_cell_ledger.__self__ is binder  # type: ignore[attr-defined]

    def test_the_binder_reads_its_config_off_the_host(
        self, host: LoopAbGatewayHost
    ) -> None:
        # Not handed in separately, so it cannot disagree with the config the
        # gateway resolves a bearer's bound provider against.
        deps = _build_deps(host)
        binder = deps.build_provider.__self__  # type: ignore[attr-defined]

        assert RECORDING_PROVIDER in binder.company_config.providers
        assert binder.company_config is host.app_state.config


class TestToolRegistry:
    def test_file_tools_are_scoped_to_the_graded_tree(self, tmp_path: Path) -> None:
        # The file tools work in the project subtree while the shell sandbox is
        # bound to the cell root and re-derives that subtree by project id. Both
        # have to land on the same directory or the brief is graded against a
        # tree the loop never wrote to.
        workspace = CellWorkspace(root=tmp_path / "cell")
        workspace.project_dir.mkdir(parents=True)

        registry = _build_tool_registry(workspace)

        file_tools = [
            tool
            for tool in registry.all_tools()
            if isinstance(tool, BaseFileSystemTool)
        ]
        assert file_tools
        # The tools resolve their root, so compare against a resolved path.
        assert {tool.workspace_root for tool in file_tools} == {
            workspace.project_dir.resolve()
        }

    async def test_the_sandbox_binds_the_root_the_project_lives_under(
        self, tmp_path: Path
    ) -> None:
        # The shell tool takes the cell root, not the project dir, because the
        # sandbox selects its own mount beneath that by the run's project id.
        # Handing it the project dir would nest the mount one level too deep.
        workspace = CellWorkspace(root=tmp_path / "cell")
        workspace.project_dir.mkdir(parents=True)

        sandbox = DockerSandbox(workspace=workspace.root)
        resolved = await sandbox._project_root(EVAL_TASK_PROJECT)

        assert resolved == workspace.project_dir


class TestPlanPath:
    def test_the_default_run_prints_a_plan_and_boots_nothing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # No --record: no port bound, no container started, no provider dialled.
        # If this ever booted the host it would refuse anyway (one host per
        # process), so a passing assertion here is also evidence it did not.
        exit_code = main([])

        assert exit_code == 0
        assert "Loop A/B recording plan" in capsys.readouterr().out

    def test_bind_host_defaults_to_resolved_rather_than_every_interface(self) -> None:
        # Unset means "work out the narrowest address the sandbox can reach".
        # A literal default here would put the whole application on the network
        # for the length of a run.
        assert _parse_args([]).bind_host is None

    def test_workspaces_are_reclaimed_unless_asked_otherwise(self) -> None:
        assert _parse_args([]).keep_workspaces is False
        assert _parse_args(["--keep-workspaces"]).keep_workspaces is True
