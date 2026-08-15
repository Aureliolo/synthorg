"""Unit tests for the leaked-test-container sweep.

The sweep issues `docker rm`, so what it selects matters more than what it
removes: the filters are the only thing standing between reclaiming debris
and deleting a container somebody else's run is using. These tests pin the
argv, not just the outcome.
"""

import subprocess
from collections.abc import Sequence

import pytest

from tests._shared.testcontainer_reclaim import reclaim_exited_testcontainers

pytestmark = pytest.mark.unit


class _RecordingRun:
    """Stands in for ``subprocess.run``, recording every argv it sees."""

    def __init__(self, listed: str, rm_returncode: int = 0) -> None:
        self.calls: list[Sequence[str]] = []
        self._listed = listed
        self._rm_returncode = rm_returncode

    def __call__(
        self, argv: Sequence[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        if "ps" in argv:
            return subprocess.CompletedProcess(argv, 0, self._listed, "")
        return subprocess.CompletedProcess(argv, self._rm_returncode, "", "")


def test_sweep_selects_only_exited_test_containers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The listing filters on the testcontainers label AND exited status.

    Either filter alone would be wrong: without the label it would sweep an
    operator's own stopped containers, and without the status it would take
    containers a concurrent run is still using.
    """
    run = _RecordingRun("abc123\ndef456\n")
    monkeypatch.setattr(subprocess, "run", run)

    removed = reclaim_exited_testcontainers()

    assert removed == ("abc123", "def456")
    listing = run.calls[0]
    assert "--all" in listing
    assert "label=org.testcontainers=true" in listing
    assert "status=exited" in listing


def test_sweep_takes_the_anonymous_volume_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removal passes ``-v``.

    Each test container leaves an anonymous volume. Removing the container
    without it turns a visible leak into one no container listing shows.
    """
    run = _RecordingRun("abc123\n")
    monkeypatch.setattr(subprocess, "run", run)

    reclaim_exited_testcontainers()

    assert ["docker", "rm", "-v", "abc123"] in run.calls


def test_sweep_is_a_no_op_when_nothing_is_listed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty listing removes nothing and issues no rm."""
    run = _RecordingRun("")
    monkeypatch.setattr(subprocess, "run", run)

    assert reclaim_exited_testcontainers() == ()
    assert all("rm" not in call for call in run.calls)


def test_sweep_survives_docker_being_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine with no docker skips rather than failing collection."""

    def _raise(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        msg = "docker not found"
        raise OSError(msg)

    monkeypatch.setattr(subprocess, "run", _raise)

    assert reclaim_exited_testcontainers() == ()


def test_a_container_that_refuses_removal_is_not_reported_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed rm is excluded from the result rather than counted."""
    run = _RecordingRun("abc123\n", rm_returncode=1)
    monkeypatch.setattr(subprocess, "run", run)

    assert reclaim_exited_testcontainers() == ()
