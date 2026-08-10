"""What this process can actually do when an agent calls a tool.

Run 3 of the dogfood lost two runs to a backend that could plan and review but
could not execute: every shelling tool died at invocation, agents kept going to
turn 16, and the run read as a model problem. The probes here exist so that
condition is stated once, at startup, in terms of what it costs, instead of
being rediscovered from a wall of failed agents.

Each reason is asserted for its CONSEQUENCE, not just its wording: a reason that
names the loop but not the tools it takes down sends an operator looking in the
wrong place, which is the failure being fixed rather than a style preference.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast, override

import aiodocker
import pytest

from synthorg.tools.sandbox.errors import SandboxWorkspaceUnmappableError
from synthorg.tools.sandbox.execution_capability import (
    ProbeOutcome,
    ToolExecutionCapability,
    probe_container_backend,
    probe_subprocess_spawn,
    probe_tool_execution,
)
from synthorg.tools.sandbox.workspace_mount import OwnContainer, WorkspaceMount
from tests._shared import FakeDockerClient, JsonDict

pytestmark = pytest.mark.unit

_CONTAINER_ID = "3ad75118a7443324ebe045e52e19a23e4d8659546e6e5a67a900d18cac149b5d"


class _FakeProcess:
    """A process that has already exited, as the spawn probe reads one."""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return (b"git version 2.51.0\n", b"")


def _spawns(*, raises: BaseException | None = None, returncode: int = 0) -> object:
    async def _spawn(*args: object, **kwargs: object) -> _FakeProcess:
        del args, kwargs
        if raises is not None:
            raise raises
        return _FakeProcess(returncode)

    return _spawn


class _ProbeDocker(FakeDockerClient):
    """A daemon that answers version and one container inspect."""

    def __init__(
        self,
        *,
        mounts: list[JsonDict] | None = None,
        version_raises: BaseException | None = None,
    ) -> None:
        self.closed = False
        self._mounts = mounts if mounts is not None else []
        self._version_raises = version_raises
        super().__init__(SimpleNamespace(container=self._container))

    def _container(self, container_id: str) -> SimpleNamespace:
        del container_id
        return SimpleNamespace(show=self._show)

    async def _show(self) -> JsonDict:
        return cast("JsonDict", {"Mounts": self._mounts})

    @override
    async def version(self) -> JsonDict:
        if self._version_raises is not None:
            raise self._version_raises
        return cast("JsonDict", {"ApiVersion": "1.55"})

    @override
    async def close(self) -> None:
        self.closed = True


class TestSubprocessProbe:
    async def test_a_loop_that_cannot_spawn_names_the_tools_it_takes_down(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            asyncio,
            "create_subprocess_exec",
            _spawns(raises=NotImplementedError()),
        )

        outcome = await probe_subprocess_spawn()

        assert outcome.available is False
        assert outcome.reason is not None
        assert "event loop" in outcome.reason
        assert "git" in outcome.reason

    async def test_a_working_loop_is_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawns())

        outcome = await probe_subprocess_spawn()

        assert outcome.available is True
        assert outcome.reason is None

    async def test_a_missing_probe_binary_is_reported_as_itself(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Not the same condition as an incapable loop, and an operator whose
        # image lost git needs to be told that rather than sent to the loop.
        monkeypatch.setattr(
            asyncio,
            "create_subprocess_exec",
            _spawns(raises=FileNotFoundError()),
        )

        outcome = await probe_subprocess_spawn()

        assert outcome.available is False
        assert outcome.reason is not None
        assert "event loop" not in outcome.reason


class TestContainerProbe:
    async def test_an_unreachable_daemon_names_the_lost_categories(
        self, tmp_path: Path
    ) -> None:
        docker = _ProbeDocker(version_raises=OSError("connection refused"))

        outcome, mount = await probe_container_backend(
            workspace=tmp_path, docker=docker, own=OwnContainer(container_id=None)
        )

        assert outcome.available is False
        assert mount is None
        assert outcome.reason is not None
        assert "terminal" in outcome.reason
        assert "code_execution" in outcome.reason
        assert "CodeExecutionRecord" in outcome.reason
        # A client the caller supplied stays the caller's to close.
        assert docker.closed is False

    async def test_a_client_the_probe_opened_is_closed_on_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The probe runs at boot and on every retry of a blocked subsystem, so
        # a leaked aiohttp session per attempt would accumulate for the life of
        # the process.
        opened: list[_ProbeDocker] = []

        class _Opening(_ProbeDocker):
            def __init__(self) -> None:
                super().__init__(version_raises=OSError("connection refused"))
                opened.append(self)

        # Patched to a real class, not a factory function: the annotation
        # ``aiodocker.Docker | None`` is evaluated at runtime by typeguard.
        monkeypatch.setattr(aiodocker, "Docker", _Opening)

        outcome, _mount = await probe_container_backend(
            workspace=tmp_path, own=OwnContainer(container_id=None)
        )

        assert outcome.available is False
        assert [client.closed for client in opened] == [True]

    async def test_a_host_with_no_daemon_at_all_is_reported_not_raised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # With no socket to find, aiodocker asserts inside its constructor
        # rather than returning. Letting that escape makes the subsystem read
        # `failed` (an activation that crashed) instead of `blocked` with a
        # reason, which is exactly the distinction this probe exists to make.
        class _Unresolvable(_ProbeDocker):
            def __init__(self) -> None:
                msg = "Missing valid docker_host."
                raise AssertionError(msg)

        monkeypatch.setattr(aiodocker, "Docker", _Unresolvable)

        outcome, mount = await probe_container_backend(
            workspace=tmp_path, own=OwnContainer(container_id=None)
        )

        assert outcome.available is False
        assert mount is None
        assert outcome.reason is not None
        assert "CodeExecutionRecord" in outcome.reason

    async def test_a_reachable_daemon_on_the_host_is_available(
        self, tmp_path: Path
    ) -> None:
        docker = _ProbeDocker()

        outcome, mount = await probe_container_backend(
            workspace=tmp_path, docker=docker, own=OwnContainer(container_id=None)
        )

        assert outcome.available is True
        assert mount is None

    async def test_a_containerised_process_carries_its_resolved_mount(self) -> None:
        docker = _ProbeDocker(
            mounts=[
                cast(
                    "JsonDict",
                    {"Type": "volume", "Name": "vol", "Destination": "/data"},
                )
            ]
        )

        outcome, mount = await probe_container_backend(
            workspace=Path("/data/agent-workspaces"),
            docker=docker,
            own=OwnContainer(container_id=_CONTAINER_ID),
        )

        assert outcome.available is True
        assert mount == WorkspaceMount(volume="vol", subpath="agent-workspaces")

    async def test_an_unmappable_workspace_names_the_empty_mount_it_avoids(
        self,
    ) -> None:
        docker = _ProbeDocker(
            mounts=[
                cast(
                    "JsonDict",
                    {"Type": "volume", "Name": "vol", "Destination": "/srv"},
                )
            ]
        )

        outcome, mount = await probe_container_backend(
            workspace=Path("/data/agent-workspaces"),
            docker=docker,
            own=OwnContainer(container_id=_CONTAINER_ID),
        )

        assert outcome.available is False
        assert mount is None
        assert outcome.reason is not None
        assert "/data/agent-workspaces" in outcome.reason
        assert "empty" in outcome.reason


class TestCapabilityReport:
    def test_both_probes_passing_can_execute(self) -> None:
        capability = ToolExecutionCapability(
            subprocess=ProbeOutcome(available=True),
            container=ProbeOutcome(available=True),
        )
        assert capability.can_execute is True
        assert capability.decline_reason is None

    def test_a_failing_probe_supplies_the_decline_reason(self) -> None:
        capability = ToolExecutionCapability(
            subprocess=ProbeOutcome(available=False, reason="no spawn"),
            container=ProbeOutcome(available=True),
        )
        assert capability.can_execute is False
        assert capability.decline_reason == "no spawn"

    def test_two_failing_probes_report_both(self) -> None:
        capability = ToolExecutionCapability(
            subprocess=ProbeOutcome(available=False, reason="no spawn"),
            container=ProbeOutcome(available=False, reason="no daemon"),
        )
        assert capability.decline_reason is not None
        assert "no spawn" in capability.decline_reason
        assert "no daemon" in capability.decline_reason

    def test_an_unavailable_outcome_must_say_why(self) -> None:
        with pytest.raises(ValueError, match="reason"):
            ProbeOutcome(available=False)


class TestProbeTogether:
    async def test_reports_both_halves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawns())

        capability = await probe_tool_execution(
            workspace=tmp_path,
            docker=_ProbeDocker(),
            own=OwnContainer(container_id=None),
        )

        assert capability.can_execute is True

    async def test_a_dead_loop_declines_even_with_a_live_daemon(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            asyncio,
            "create_subprocess_exec",
            _spawns(raises=NotImplementedError()),
        )

        capability = await probe_tool_execution(
            workspace=tmp_path,
            docker=_ProbeDocker(),
            own=OwnContainer(container_id=None),
        )

        assert capability.can_execute is False
        assert capability.decline_reason is not None

    async def test_an_unmappable_workspace_is_not_raised_at_the_caller(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The probe's whole job is to REPORT the condition; letting the typed
        # error escape would make the subsystem read `failed` (an activation
        # that crashed) rather than `blocked` (a condition it can name).
        monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawns())
        docker = _ProbeDocker(
            mounts=[
                cast(
                    "JsonDict",
                    {"Type": "volume", "Name": "vol", "Destination": "/srv"},
                )
            ]
        )

        capability = await probe_tool_execution(
            workspace=Path("/data/agent-workspaces"),
            docker=docker,
            own=OwnContainer(container_id=_CONTAINER_ID),
        )

        assert capability.can_execute is False
        assert isinstance(capability.container.error, SandboxWorkspaceUnmappableError)
