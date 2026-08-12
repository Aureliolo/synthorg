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

import pytest
from scripts.record_loop_ab import _build_deps, _parse_args, main

from evals.loop_ab.binding import CellBinder
from evals.loop_ab.host import LoopAbGatewayHost
from evals.loop_ab.stall_watch import DEFAULT_STALL_IDLE_SECONDS
from tests.evals_spine.loop_ab.conftest import RECORDING_PROVIDER

pytestmark = [
    pytest.mark.integration,
    # The deps-wiring and plan-path tests boot the recording host for real.
    pytest.mark.slow,
    pytest.mark.timeout(300),
]


class TestDepsWiring:
    def test_every_collaborator_is_bound_to_the_host(
        self, host: LoopAbGatewayHost
    ) -> None:
        deps = _build_deps(host)

        # Bound at all: a dropped line here is invisible to the type checker
        # because every one of these fields defaults.
        assert deps.build_openhands_cell is not None
        assert deps.open_cell_ledger is not None
        assert deps.project_repo is not None
        assert deps.on_stall is not None

    def test_the_stall_threshold_reaches_the_cells(
        self, host: LoopAbGatewayHost
    ) -> None:
        # A threshold the operator set and the runner never read would leave a
        # wedged cell reported on the default, or not at all.
        deps = _build_deps(host, stall_idle_seconds=42.0)

        assert deps.stall_idle_seconds == 42.0

    def test_the_bound_methods_come_from_one_binder_over_this_host(
        self, host: LoopAbGatewayHost
    ) -> None:
        # Bound to the RIGHT thing: four methods of one binder over the started
        # host, not a binder over some other config, and not swapped with each
        # other (which would type-check, since two of them take a CellRun).
        deps = _build_deps(host)
        assert deps.build_openhands_cell is not None
        assert deps.open_cell_ledger is not None

        binder = deps.build_provider.__self__  # type: ignore[attr-defined]

        assert isinstance(binder, CellBinder)
        assert binder.host is host
        assert deps.build_provider.__func__ is CellBinder.build_provider  # type: ignore[attr-defined]
        assert deps.build_tool_registry.__func__ is CellBinder.build_tool_registry  # type: ignore[attr-defined]
        assert deps.build_openhands_cell.__func__ is CellBinder.build_openhands_cell  # type: ignore[attr-defined]
        assert deps.open_cell_ledger.__func__ is CellBinder.open_cell_ledger  # type: ignore[attr-defined]
        assert deps.build_tool_registry.__self__ is binder  # type: ignore[attr-defined]
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

    def test_every_image_a_leg_runs_on_can_be_stated(self) -> None:
        # Nothing under ``synthorg.tools.sandbox`` pulls, and the registered
        # defaults track the running version rather than a tag that is
        # guaranteed to exist, so each of the three has to be nameable. Unset
        # means "whatever this instance resolves", never a frozen constant.
        args = _parse_args(
            [
                "--openhands-image",
                "example.invalid/openhands:pinned",
                "--sandbox-image",
                "example.invalid/sandbox:pinned",
                "--sidecar-image",
                "example.invalid/sidecar:pinned",
            ]
        )

        assert args.openhands_image == "example.invalid/openhands:pinned"
        assert args.sandbox_image == "example.invalid/sandbox:pinned"
        assert args.sidecar_image == "example.invalid/sidecar:pinned"
        assert _parse_args([]).openhands_image is None
        assert _parse_args([]).sandbox_image is None
        assert _parse_args([]).sidecar_image is None

    def test_the_stall_threshold_is_a_notification_not_a_deadline(self) -> None:
        # Named as a notification because that is all it is: nothing in the
        # harness ends a run on it, and a flag called a timeout would read as a
        # promise the recorder does not make.
        assert _parse_args([]).stall_notify_seconds == DEFAULT_STALL_IDLE_SECONDS
        assert (
            _parse_args(["--stall-notify-seconds", "90"]).stall_notify_seconds == 90.0
        )
