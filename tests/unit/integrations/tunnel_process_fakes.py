"""Shared ``subprocess.Popen`` fake for tunnel adapter tests.

Subclasses the real ``Popen`` (without calling its ``__init__``, which
would spawn a process) so typeguard's isinstance checks at the
``_process`` helper boundaries pass. Streams are ``BytesIO``: readline
returns queued lines, then EOF, which matches how the helpers scan and
drain vendor-CLI output.
"""

import io
import subprocess
from typing import override


class FakePopen(subprocess.Popen[bytes]):
    """In-memory CLI process double."""

    def __init__(
        self,
        *,
        stdout_lines: list[str] | None = None,
        stderr_lines: list[str] | None = None,
        returncode: int | None = None,
        hang_until_kill: bool = False,
    ) -> None:
        self.stdout = io.BytesIO("".join(stdout_lines or []).encode("utf-8"))
        self.stderr = io.BytesIO("".join(stderr_lines or []).encode("utf-8"))
        self._rc = returncode
        self.terminated = False
        self.killed = False
        # Simulates a child that ignores SIGTERM: ``terminate`` leaves it
        # alive and ``wait(timeout=...)`` raises ``TimeoutExpired`` until
        # ``kill`` lands, so the kill-escalation path is exercisable.
        self._hang_until_kill = hang_until_kill
        # Popen.__del__ inspects this; without it a GC'd fake warns.
        self._child_created = False

    @property
    @override
    def returncode(self) -> int | None:  # type: ignore[override]
        return self._rc

    @override
    def poll(self) -> int | None:
        return self._rc

    @override
    def wait(self, timeout: float | None = None) -> int:
        if self._rc is None and self._hang_until_kill:
            raise subprocess.TimeoutExpired(cmd="fake-cli", timeout=timeout or 0)
        self._rc = self._rc if self._rc is not None else 0
        return self._rc

    @override
    def terminate(self) -> None:
        self.terminated = True
        if not self._hang_until_kill:
            self._rc = 0

    @override
    def kill(self) -> None:
        self.killed = True
        self._hang_until_kill = False
        self._rc = -9
