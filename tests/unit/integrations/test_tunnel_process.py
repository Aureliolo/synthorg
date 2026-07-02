"""Tests for the shared tunnel subprocess helpers."""

import pytest

from synthorg.integrations.tunnel import _process
from synthorg.integrations.tunnel._process import terminate_process
from tests.unit.integrations.tunnel_process_fakes import FakePopen

pytestmark = pytest.mark.unit


class TestTerminateProcess:
    async def test_graceful_terminate_skips_kill(self) -> None:
        process = FakePopen()
        await terminate_process(process)
        assert process.terminated is True
        assert process.killed is False

    async def test_already_exited_child_is_left_alone(self) -> None:
        process = FakePopen(returncode=0)
        await terminate_process(process)
        assert process.terminated is False
        assert process.killed is False

    async def test_sigterm_ignoring_child_escalates_to_kill(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A child that survives the grace period gets SIGKILLed."""
        monkeypatch.setattr(_process, "_TERMINATE_GRACE_SECONDS", 0.01)
        process = FakePopen(hang_until_kill=True)
        await terminate_process(process)
        assert process.terminated is True
        assert process.killed is True
        assert process.returncode == -9
