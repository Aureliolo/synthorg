# module-kind: tests
"""A recording refuses to start when its sandbox image does not resolve.

The daemon already answered this question at boot and the answer was logged
rather than acted on, on the reasoning that an absent image "fails on its first
container". It does not: planning and the contract stage run through the
gateway and open no container, so the failure surfaces at GRADING with every
session already paid for. Two cells died exactly that way.
"""

from typing import Never

import aiodocker
import pytest

from evals.errors import HarnessImageUnresolvedError
from evals.recursion_depth.preflight import check_images_resolve

pytestmark = pytest.mark.unit

_PRESENT = "ghcr.io/example/sandbox@sha256:abc"
_ABSENT = "ghcr.io/example/sandbox:v0.9.3"


class _Images:
    """The images half of a daemon client, scripted by reference."""

    def __init__(self, known: set[str]) -> None:
        self._known = known

    async def inspect(self, reference: str) -> dict[str, str]:
        """Answer as the daemon does: a record, or its own error.

        Returns:
            The inspected record.

        Raises:
            DockerError: The daemon holds nothing under this reference.
        """
        if reference in self._known:
            return {"Id": "sha256:deadbeef"}
        raise aiodocker.DockerError(404, f"No such image: {reference}")


class _UnreachableImages:
    """An images client whose socket has gone away."""

    async def inspect(self, reference: str) -> Never:
        """Fail the way a closed socket does.

        Raises:
            OSError: Always.
        """
        msg = f"socket closed while inspecting {reference}"
        raise OSError(msg)


class _Daemon:
    """An async-context daemon client over a fixed images half."""

    def __init__(self, images: _Images | _UnreachableImages) -> None:
        self.images = images

    async def __aenter__(self) -> _Daemon:
        """Enter the client context.

        Returns:
            Itself.
        """
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Leave the client context."""
        return


def _daemon_holding(monkeypatch: pytest.MonkeyPatch, *references: str) -> None:
    """Point the module's daemon client at a fixed reference set."""
    monkeypatch.setattr(
        aiodocker,
        "Docker",
        lambda: _Daemon(_Images(set(references))),
    )


class TestAnAbsentImageStopsTheRunBeforeItSpends:
    """The point of the check is WHEN it fires, not that it fires."""

    async def test_a_reference_the_daemon_does_not_hold_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _daemon_holding(monkeypatch, _PRESENT)

        with pytest.raises(HarnessImageUnresolvedError, match=_ABSENT):
            await check_images_resolve((_ABSENT,))

    async def test_the_refusal_names_every_missing_reference(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # One at a time would send an operator round the loop twice, and the
        # loop costs a boot.
        _daemon_holding(monkeypatch)

        with pytest.raises(HarnessImageUnresolvedError) as caught:
            await check_images_resolve((_ABSENT, "ghcr.io/example/sidecar:v1"))

        assert _ABSENT in str(caught.value)
        assert "sidecar" in str(caught.value)

    async def test_the_refusal_says_a_tag_can_stop_resolving_on_its_own(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The remedy is the message's job: nothing here changed.

        An operator reading "no such image" against a reference their manifest
        has always carried needs to be told that a published tag moving is the
        expected cause, or the next hour goes on looking for their own edit.
        """
        _daemon_holding(monkeypatch)

        with pytest.raises(HarnessImageUnresolvedError) as caught:
            await check_images_resolve((_ABSENT,))

        assert "--sandbox-image" in str(caught.value)
        assert "digest" in str(caught.value)

    async def test_a_resolvable_reference_passes_quietly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _daemon_holding(monkeypatch, _PRESENT)

        await check_images_resolve((_PRESENT,))

    async def test_an_unreachable_daemon_counts_as_missing_rather_than_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An OS-level failure is still "cannot be graded", not a crash.

        The daemon's own reachability is a separate check with its own error;
        by the time this runs it has answered once already, so a socket error
        here is a reference this run cannot use.
        """
        monkeypatch.setattr(
            aiodocker,
            "Docker",
            lambda: _Daemon(_UnreachableImages()),
        )

        with pytest.raises(HarnessImageUnresolvedError):
            await check_images_resolve((_PRESENT,))
