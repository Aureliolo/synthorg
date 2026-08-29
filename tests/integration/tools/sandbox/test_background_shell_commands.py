"""Integration tests for the background-job wrapper against a real container.

These exercise :mod:`synthorg.tools.sandbox._background_wrapper` directly
over an ``aiodocker`` exec, the same primitive
:class:`~synthorg.tools.sandbox.docker_sandbox_exec.DockerSandboxExecMixin`
uses, ahead of the full :class:`DockerSandboxBackgroundMixin` (a later
phase). The wrapper's own quoting is the highest-risk piece of this
feature -- a second layer of shell quoting around an arbitrary
agent-authored command line is where injection or truncation bugs
would hide -- and that risk is real: an earlier revision of
``build_start_command`` passed unit tests built on the same argv but
silently orphaned the tracked pid from its own process group whenever
a `cd` prefix or output redirection reached bash's ``-c`` invocation
directly, discovered only by running the built commands against a
real container. Everything here therefore runs for real, never
against a mock.
"""

import asyncio
import time
from typing import Final

import pytest

from synthorg.tools.sandbox._background_wrapper import (
    build_kill_command,
    build_liveness_command,
    build_read_output_command,
    build_start_command,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(60)]

_TEST_IMAGE = "python:3.12-slim"

#: aiodocker's own multiplexed-frame stream tags for a non-TTY exec.
_EXEC_STREAM_STDERR: Final[int] = 2


def _docker_and_image_available() -> bool:
    """Check if Docker daemon is reachable and test image exists.

    Returns:
        Whether a real container can be started for this suite.
    """
    try:
        import aiodocker

        async def _check() -> bool:
            client = None
            try:
                client = aiodocker.Docker()
                await client.version()
                await client.images.inspect(_TEST_IMAGE)
            except Exception:
                return False
            else:
                return True
            finally:
                if client is not None:
                    await client.close()

        return asyncio.run(_check())
    except Exception:
        return False


skip_no_docker = pytest.mark.skipif(
    not _docker_and_image_available(),
    reason=f"Docker daemon not available or {_TEST_IMAGE} not pulled",
)


async def _run(container: object, command: str, args: tuple[str, ...]) -> str:
    """Run (command, args) as an attached exec and return its stdout.

    Mirrors ``DockerSandboxExecMixin._open_exec``/``_collect_exec_output``:
    a non-TTY exec multiplexes stdout/stderr frames tagged by
    ``message.stream``, and the stream is drained via ``read_out()``
    until EOF (``None``) then explicitly closed, never entered as a
    context manager.

    Returns:
        The exec's captured stdout (stderr is discarded; callers that
        need it read the wrapper's own output file instead, exactly as
        production does).
    """
    exec_obj = await container.exec(  # type: ignore[attr-defined]
        cmd=[command, *args], stdout=True, stderr=True, stdin=False, tty=False
    )
    stream = exec_obj.start(detach=False)
    stdout_parts: list[str] = []
    try:
        while True:
            message = await stream.read_out()
            if message is None:
                break
            raw = message.data
            text = (
                raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            )
            if message.stream != _EXEC_STREAM_STDERR:
                stdout_parts.append(text)
    finally:
        await stream.close()
    return "".join(stdout_parts)


async def _run_until_nonempty(
    container: object,
    command: str,
    args: tuple[str, ...],
    *,
    timeout: float = 5.0,  # noqa: ASYNC109
) -> str:
    """Poll an exec until it returns non-empty stdout.

    Returns:
        The first non-empty stdout observed, or ``""`` at *timeout*.
    """
    deadline = time.monotonic() + timeout
    output = ""
    while time.monotonic() < deadline:
        output = await _run(container, command, args)
        if output:
            return output
        await asyncio.sleep(0.1)
    return output


@skip_no_docker
class TestBackgroundWrapperRealContainer:
    """Real-container coverage for the background-job wrapper module."""

    async def _make_container(self) -> tuple[object, object]:
        """Start a disposable container for one test.

        Returns:
            ``(docker_client, container)``.
        """
        import aiodocker

        docker = aiodocker.Docker()
        container = await docker.containers.create(
            {"Image": _TEST_IMAGE, "Cmd": ["sleep", "300"]}
        )
        await container.start()
        return docker, container

    async def _cleanup(self, docker: object, container: object) -> None:
        await container.delete(force=True)  # type: ignore[attr-defined]
        await docker.close()  # type: ignore[attr-defined]

    async def test_captures_output_and_exit_code(self) -> None:
        docker, container = await self._make_container()
        try:
            program, args = build_start_command(
                "job1",
                "echo one; echo two 1>&2; sleep 1; echo three",
                container_cwd="/tmp",  # noqa: S108
            )
            pid_line = await _run(container, program, args)
            assert pid_line.strip().isdigit()

            program, args = build_liveness_command("job1")
            status = await _run_until_nonempty(container, program, args)
            assert status.strip() == "0"

            program, args = build_read_output_command("job1", byte_cap=1000)
            output = await _run(container, program, args)
            assert output == "one\ntwo\nthree\n"
        finally:
            await self._cleanup(docker, container)

    async def test_cancellation_kills_and_reaps_the_process(self) -> None:
        docker, container = await self._make_container()
        try:
            program, args = build_start_command(
                "job2",
                "sleep 30",
                container_cwd="/tmp",  # noqa: S108
            )
            pid_line = await _run(container, program, args)
            pid = int(pid_line.strip())

            program, args = build_kill_command(pid, grace_seconds=0.1)
            await _run(container, program, args)

            program, args = build_liveness_command("job2")
            status = await _run_until_nonempty(container, program, args)
            # SIGTERM: 128 + 15.
            assert status.strip() == "143"

            alive = await _run(
                container, "sh", ("-c", f"test -e /proc/{pid} && echo ALIVE")
            )
            assert alive.strip() != "ALIVE"
        finally:
            await self._cleanup(docker, container)

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            (
                "echo 'has a single quote: '\\''here'\\'''",
                "has a single quote: 'here'\n",
            ),
            (
                'echo "has double quotes and $vars literally"',
                "has double quotes and  literally\n",
            ),
            ("printf 'line1\\nline2\\n'", "line1\nline2\n"),
            ("echo a && echo b", "a\nb\n"),
            ("echo a; echo b", "a\nb\n"),
            ("echo a | cat", "a\n"),
            ("echo $(echo nested)", "nested\n"),
        ],
    )
    async def test_survives_adversarial_command_text(
        self, command: str, expected: str
    ) -> None:
        """The wrapper's own quoting must not corrupt the inner command.

        Regression coverage for the pgrp-orphaning bug this module's
        docstring documents: an earlier revision passed every unit
        test built on the same argv while silently losing process-group
        tracking, caught only by running these exact commands here.
        """
        docker, container = await self._make_container()
        try:
            program, args = build_start_command(
                "adv",
                command,
                container_cwd="/tmp",  # noqa: S108
            )
            pid_line = await _run(container, program, args)
            assert pid_line.strip().isdigit()

            program, args = build_liveness_command("adv")
            status = await _run_until_nonempty(container, program, args)
            assert status.strip() == "0"

            program, args = build_read_output_command("adv", byte_cap=1000)
            output = await _run(container, program, args)
            assert output == expected
        finally:
            await self._cleanup(docker, container)
