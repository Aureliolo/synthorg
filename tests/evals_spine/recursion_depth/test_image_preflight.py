# module-kind: tests
"""A recording refuses to start when its sandbox image does not resolve.

The daemon answers this at boot and the answer was logged rather than acted
on, on the reasoning that an absent image "fails on its first container". It
does fail there, and that is not early enough: planning runs entirely through
the gateway, so a cell buys a whole plan first. A queued cell did exactly
that, spending 85,555 tokens before dying on `[404] No such image`.

What the tests below pin is therefore WHEN the refusal happens and WHICH
condition it names, not merely that one happens.
"""

import re
from typing import Never

import aiodocker
import pytest

from evals.errors import HarnessDockerUnavailableError, HarnessImageUnresolvedError
from evals.recursion_depth.preflight import check_images_resolve

pytestmark = pytest.mark.unit

_PRESENT = "ghcr.io/example/sandbox@sha256:abc"
_ABSENT = "ghcr.io/example/sandbox:v0.9.3"


class _Images:
    """The images half of a daemon client, scripted by reference."""

    def __init__(self, known: set[str]) -> None:
        self._known = known
        self.attempts = 0

    async def inspect(self, reference: str) -> dict[str, str]:
        """Answer as the daemon does: a record, or its own error.

        Returns:
            The inspected record.

        Raises:
            DockerError: The daemon holds nothing under this reference.
        """
        self.attempts += 1
        if reference in self._known:
            return {"Id": "sha256:deadbeef"}
        raise aiodocker.DockerError(404, f"No such image: {reference}")


class _UnreachableImages:
    """An images client whose socket has gone away."""

    def __init__(self) -> None:
        self.attempts = 0

    async def inspect(self, reference: str) -> Never:
        """Fail the way a closed socket does.

        Raises:
            OSError: Always.
        """
        self.attempts += 1
        msg = f"socket closed while inspecting {reference}"
        raise OSError(msg)


class _FailingImages:
    """A daemon answering with a status that is not its own 404."""

    def __init__(self, status: int) -> None:
        self._status = status
        self.attempts = 0

    async def inspect(self, reference: str) -> Never:
        """Fail the way a broken daemon does.

        Raises:
            DockerError: Always, with the configured status.
        """
        self.attempts += 1
        raise aiodocker.DockerError(self._status, f"boom: {reference}")


class _FlakyImages:
    """A daemon that stalls a fixed number of times, then answers."""

    def __init__(self, *, failures: int, known: set[str]) -> None:
        self._failures = failures
        self._known = known
        self.attempts = 0

    async def inspect(self, reference: str) -> dict[str, str]:
        """Fail until the configured budget is spent, then answer.

        Returns:
            The inspected record.

        Raises:
            OSError: While attempts remain in the failure budget.
            DockerError: The daemon holds nothing under this reference.
        """
        self.attempts += 1
        if self.attempts <= self._failures:
            msg = f"daemon restarting while inspecting {reference}"
            raise OSError(msg)
        if reference in self._known:
            return {"Id": "sha256:deadbeef"}
        raise aiodocker.DockerError(404, f"No such image: {reference}")


class _Daemon:
    """An async-context daemon client over a fixed images half."""

    def __init__(
        self, images: _Images | _UnreachableImages | _FailingImages | _FlakyImages
    ) -> None:
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

        # Escaped: `match` is a regex, and an unescaped `.` here would match
        # any character, so the assertion would pass on a reference that only
        # resembled the missing one.
        with pytest.raises(HarnessImageUnresolvedError, match=re.escape(_ABSENT)):
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

    async def test_a_mixed_set_reports_only_what_is_actually_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The loop must keep going past a hit, and past a miss.
        _daemon_holding(monkeypatch, _PRESENT)

        with pytest.raises(HarnessImageUnresolvedError) as caught:
            await check_images_resolve((_PRESENT, _ABSENT))

        assert _ABSENT in str(caught.value)
        assert _PRESENT not in str(caught.value)


class TestADaemonThatCannotAnswerIsNotAMissingImage:
    """The two conditions have two different remedies, so two errors.

    Reporting a socket error or a 500 as "no such image" sends an operator to
    rebuild an image that is fine, and it does so AFTER tearing down a booted
    host to say it. ``workspace_mount`` already draws this line on the same
    call for the same reason.
    """

    async def test_an_unreachable_daemon_is_reported_as_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            aiodocker,
            "Docker",
            lambda: _Daemon(_UnreachableImages()),
        )

        with pytest.raises(HarnessDockerUnavailableError):
            await check_images_resolve((_PRESENT,))

    async def test_a_server_error_is_not_read_as_an_absent_image(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A 500 says the daemon failed, not that it looked and found nothing.
        monkeypatch.setattr(
            aiodocker,
            "Docker",
            lambda: _Daemon(_FailingImages(500)),
        )

        with pytest.raises(HarnessDockerUnavailableError):
            await check_images_resolve((_PRESENT,))

    async def test_a_blip_is_retried_rather_than_ending_the_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A daemon mid-restart must not cost a booted host.

        This check runs after the host is up, so refusing is not free: a
        single stalled inspect would tear down a live gateway and send the
        operator back to the start of a run that was fine.
        """
        images = _FlakyImages(failures=2, known={_PRESENT})
        monkeypatch.setattr(aiodocker, "Docker", lambda: _Daemon(images))

        await check_images_resolve((_PRESENT,))

        assert images.attempts == 3

    async def test_a_definitive_absence_is_not_retried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Re-asking a 404 cannot change the answer, and every retry here is
        # latency an operator waits through before being told what is wrong.
        images = _Images(set())
        monkeypatch.setattr(aiodocker, "Docker", lambda: _Daemon(images))

        with pytest.raises(HarnessImageUnresolvedError):
            await check_images_resolve((_ABSENT,))

        assert images.attempts == 1
