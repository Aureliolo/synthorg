"""A sandbox is released by its own owner and by nobody else.

The regression these pin cost two paid cells. ``open_sandboxes`` was one list
shared by the whole binder and the release took all of it, so at
``--leaf-concurrency 4`` the first leaf to finish tore down the sandboxes of the
three still running. ``DockerSandboxBackend.cleanup()`` latches a shutdown flag
nothing clears, so those leaves spent the rest of their budget retrying a shell
that could never come back, and were recorded as having delivered nothing.
"""

from pathlib import Path
from typing import Self, override

import pytest

from evals.harness.binding import HarnessBinder
from evals.harness.host import RecordingGatewayHost
from synthorg.persistence.state import persistence_of
from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.lifecycle.config import (
    LifecycleStrategy,
    SandboxLifecycleConfig,
)
from synthorg.tools.terminal.shell_command import ShellCommandTool
from tests._shared import mock_of


class _FakeSandbox:
    """Counts its own teardowns, which is the whole assertion here."""

    def __init__(self) -> None:
        self.cleaned = 0

    async def cleanup(self) -> None:
        self.cleaned += 1


class _RaisingSandbox(_FakeSandbox):
    """Fails teardown, to pin that a sibling is still released."""

    @override
    async def cleanup(self) -> None:
        self.cleaned += 1
        msg = "teardown refused"
        raise RuntimeError(msg)


@pytest.fixture(name="binder")
def _binder() -> HarnessBinder:
    return HarnessBinder(host=mock_of[RecordingGatewayHost]())


def _track(binder: HarnessBinder, owner: str) -> _FakeSandbox:
    """Register a fake under *owner* the way a real build would."""
    sandbox = _FakeSandbox()
    binder.track_sandbox(sandbox, owner=owner)  # type: ignore[arg-type]
    return sandbox


@pytest.mark.unit
class TestSandboxOwnership:
    """Release is scoped to one owner."""

    async def test_release_leaves_another_owners_sandbox_alone(
        self, binder: HarnessBinder
    ) -> None:
        """The concurrency-4 defect, stated as a test."""
        finishing = _track(binder, "leaf-a")
        still_running = _track(binder, "leaf-b")

        await binder.release_tool_sandboxes("leaf-a")

        assert finishing.cleaned == 1
        assert still_running.cleaned == 0

    async def test_release_covers_every_sandbox_of_one_owner(
        self, binder: HarnessBinder
    ) -> None:
        """An owner may hold more than one, and all of them go."""
        first = _track(binder, "leaf-a")
        second = _track(binder, "leaf-a")

        await binder.release_tool_sandboxes("leaf-a")

        assert (first.cleaned, second.cleaned) == (1, 1)

    async def test_release_is_idempotent_for_one_owner(
        self, binder: HarnessBinder
    ) -> None:
        """A second release must not clean the same container twice."""
        sandbox = _track(binder, "leaf-a")

        await binder.release_tool_sandboxes("leaf-a")
        await binder.release_tool_sandboxes("leaf-a")

        assert sandbox.cleaned == 1

    async def test_release_of_an_unknown_owner_is_not_an_error(
        self, binder: HarnessBinder
    ) -> None:
        """A session whose registry built nothing still runs its release."""
        await binder.release_tool_sandboxes("never-opened")

    async def test_a_failed_teardown_does_not_strand_its_siblings(
        self, binder: HarnessBinder
    ) -> None:
        """One raise would otherwise leak every container behind it."""
        failing = _RaisingSandbox()
        binder.track_sandbox(failing, owner="leaf-a")  # type: ignore[arg-type]
        surviving = _track(binder, "leaf-a")

        await binder.release_tool_sandboxes("leaf-a")

        assert (failing.cleaned, surviving.cleaned) == (1, 1)

    async def test_release_all_takes_every_owner(self, binder: HarnessBinder) -> None:
        """The run-end sweep is what reclaims whatever a failure left open."""
        first = _track(binder, "leaf-a")
        second = _track(binder, "grade:leaf-a")

        await binder.release_all_sandboxes()

        assert (first.cleaned, second.cleaned) == (1, 1)

    async def test_release_all_leaves_nothing_tracked(
        self, binder: HarnessBinder
    ) -> None:
        """A second sweep must not re-clean what the first one released."""
        sandbox = _track(binder, "leaf-a")

        await binder.release_all_sandboxes()
        await binder.release_all_sandboxes()

        assert sandbox.cleaned == 1


class _StubWorkspace:
    """Enough of a workspace for the registry build to root its tools."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def __enter__(self) -> Self:
        return self


def _arm_host(binder: HarnessBinder) -> None:
    """Give the mocked host the few real values a sandbox build reads.

    The lifecycle has to be a real config: the factory matches on its name and
    raises on anything else, so a bare mock never reaches the tracking this
    module is about.
    """
    host = binder.host
    host.sandbox_image = "sandbox:test"  # type: ignore[misc]
    host.sidecar_image = "sidecar:test"  # type: ignore[misc]
    # Assigned through the spec'd mock, which reports the real config's fields
    # as the frozen properties they are. The mock is a plain attribute bag at
    # runtime; only the checker sees a model here.
    host.app_state.config.sandboxing.docker = DockerSandboxConfig(  # type: ignore[misc]
        lifecycle=SandboxLifecycleConfig(strategy=LifecycleStrategy.PER_CALL)
    )


@pytest.mark.unit
class TestConstructionRegistersTheOwner:
    """Both construction seams file their sandbox under the owner they were given."""

    def test_tool_registry_files_its_sandbox_under_the_session(
        self, binder: HarnessBinder, tmp_path: Path
    ) -> None:
        """The agent's shell is owned by the session that opened it."""
        _arm_host(binder)

        workspace = _StubWorkspace(tmp_path)
        binder.build_tool_registry(workspace, owner="exec-1")  # type: ignore[arg-type]

        assert binder.sandbox_owners() == ("exec-1",)

    def test_graded_sandbox_files_under_its_own_owner(
        self, binder: HarnessBinder, tmp_path: Path
    ) -> None:
        """Grading outlives the session, so it cannot share the session's key."""
        _arm_host(binder)

        binder.build_sandbox(tmp_path, owner="grade:unit-1")

        assert binder.sandbox_owners() == ("grade:unit-1",)


@pytest.mark.unit
class TestTheShellToolIsGateEvidence:
    """The agent's shell writes the rows the build/test oracle reads.

    Built without the record store, a session's every ``pytest`` left nothing
    behind and the product's review failed every leaf closed on "no test run";
    the product's own factory hands the tool the store and the workspace root,
    so the harness must too or it measures a loop the product does not run.
    """

    def test_the_shell_tool_carries_the_record_store_and_the_root(
        self, binder: HarnessBinder, tmp_path: Path
    ) -> None:
        _arm_host(binder)
        workspace = _StubWorkspace(tmp_path)

        registry = binder.build_tool_registry(workspace, owner="exec-1")  # type: ignore[arg-type]

        shell = registry.get("shell_command")
        assert isinstance(shell, ShellCommandTool)
        expected = persistence_of(binder.host.app_state).code_execution_records
        assert shell._code_execution_records is expected
        assert shell._workspace_root == tmp_path
