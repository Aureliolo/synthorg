"""What the embedded git backend does when git does not cooperate.

The happy paths run against real git in the integration tier. These pin the
failure and retry behaviour, which needs git to fail on demand and so cannot
be driven by a real repository: what is under test is which exception comes
out, how many attempts were made, and whether a transfer that cannot answer
the question asked says so.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Final

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    GitBackendFetchError,
    GitBackendPushError,
    GitBackendSeedError,
)
from synthorg.engine.workspace.git_backend import _ref_transfer
from synthorg.engine.workspace.git_backend._ref_transfer import (
    GitFailure,
    transfer_ref_local,
)
from synthorg.engine.workspace.git_backend.config import GitBackendResilienceConfig
from synthorg.engine.workspace.git_backend.embedded import EmbeddedGitBackend
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit

_PROJECT: Final[str] = "proj-embedded"
_HEAD: Final[str] = "a" * 40
_EMBEDDED: Final[str] = "synthorg.engine.workspace.git_backend.embedded"
_TRANSFER_REFUSED: Final[str] = "git said no"

#: No sleeping between attempts: the retry policy is what is under test, not
#: the backoff arithmetic, which has its own suite.
_NO_BACKOFF: Final[GitBackendResilienceConfig] = GitBackendResilienceConfig(
    max_attempts=3, base_delay_seconds=0.0, cap_delay_seconds=0.1, jitter=False
)


def _backend(
    tmp_path: Path,
    *,
    resilience: GitBackendResilienceConfig | None = None,
) -> EmbeddedGitBackend:
    return EmbeddedGitBackend(
        base_root=tmp_path,
        embedded_subdir="repos",
        cmd_timeout=5.0,
        resilience=resilience,
        clock=FakeClock(),
    )


class _CountingTransfer:
    """Stands in for ``transfer_ref_local``, failing a fixed number of times."""

    def __init__(self, *, failures: int, exc: type[Exception]) -> None:
        self._remaining = failures
        self._exc = exc
        self.calls = 0

    async def __call__(self, **_kwargs: object) -> None:
        self.calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise self._exc(_TRANSFER_REFUSED)


class TestTransientFailuresRetry:
    """A local transport still has transient failures, so it still retries.

    ``GitBackendPushError`` and ``GitBackendFetchError`` are declared
    retryable, and the sibling external-remote backend honours that with a
    ``GeneralRetryHandler``. The embedded backend is the DEFAULT, so the same
    error type meant "retried" or "failed at once" depending on which backend
    an operator had configured. A bare repo on a shared volume loses a race
    for ``index.lock`` exactly the way a remote one does.
    """

    async def test_a_push_retries_and_then_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        transfer = _CountingTransfer(failures=2, exc=GitBackendPushError)
        monkeypatch.setattr(f"{_EMBEDDED}.transfer_ref_local", transfer)
        monkeypatch.setattr(f"{_EMBEDDED}.git", _fixed_git(_HEAD))
        result = await _backend(tmp_path, resilience=_NO_BACKOFF).push(
            project_id=NotBlankStr(_PROJECT),
            repo_root=tmp_path / "work",
            branch=NotBlankStr("feature"),
            base_branch=NotBlankStr("main"),
        )
        assert transfer.calls == 3
        assert result.head_sha == _HEAD

    async def test_a_push_that_never_succeeds_raises_after_the_budget(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bounded: a failing transport must not retry for ever."""
        transfer = _CountingTransfer(failures=99, exc=GitBackendPushError)
        monkeypatch.setattr(f"{_EMBEDDED}.transfer_ref_local", transfer)
        with pytest.raises(GitBackendPushError):
            await _backend(tmp_path, resilience=_NO_BACKOFF).push(
                project_id=NotBlankStr(_PROJECT),
                repo_root=tmp_path / "work",
                branch=NotBlankStr("feature"),
                base_branch=NotBlankStr("main"),
            )
        assert transfer.calls == _NO_BACKOFF.max_attempts

    async def test_a_fetch_retries_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        transfer = _CountingTransfer(failures=1, exc=GitBackendFetchError)
        monkeypatch.setattr(f"{_EMBEDDED}.transfer_ref_local", transfer)
        result = await _backend(tmp_path, resilience=_NO_BACKOFF).fetch(
            project_id=NotBlankStr(_PROJECT),
            repo_root=tmp_path / "work",
            branch=NotBlankStr("feature"),
        )
        assert transfer.calls == 2
        assert result.updated_refs == ("feature",)

    async def test_a_seed_failure_is_not_retried(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Seeding is a one-shot import, and its error is not retryable.

        Retrying it would re-run an import onto a tree the failed attempt may
        have already half-written, which is a different and worse failure than
        the one being recovered from.
        """
        transfer = _CountingTransfer(failures=99, exc=GitBackendSeedError)
        monkeypatch.setattr(f"{_EMBEDDED}.transfer_ref_local", transfer)
        with pytest.raises(GitBackendSeedError):
            await _backend(tmp_path, resilience=_NO_BACKOFF).push(
                project_id=NotBlankStr(_PROJECT),
                repo_root=tmp_path / "work",
                branch=NotBlankStr("feature"),
                base_branch=NotBlankStr("main"),
            )
        assert transfer.calls == 1


class TestBranchlessFetchIsRefused:
    """The bundle transport carries the refs it was asked for, and no others.

    So "fetch everything the remote has" is a question it cannot answer. The
    sibling external-remote backend answers the same call with a real full
    fetch, so returning an empty success here would make one protocol method
    mean two different things depending on configuration.
    """

    async def test_no_branch_raises_rather_than_reporting_success(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(GitBackendFetchError, match="needs a branch"):
            await _backend(tmp_path).fetch(
                project_id=NotBlankStr(_PROJECT),
                repo_root=tmp_path / "work",
                branch=None,
            )


class _ScriptedGit:
    """Replays a fixed sequence of ``git`` outcomes, recording the argv seen."""

    def __init__(self, outcomes: Sequence[str | Exception]) -> None:
        self._outcomes = list(outcomes)
        self.argv: list[tuple[str, ...]] = []

    async def __call__(self, _root: Path, *args: str, **_kwargs: object) -> str:
        self.argv.append(args)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _fixed_git(value: str) -> _ScriptedGit:
    return _ScriptedGit([value] * 8)


class TestTransferFailurePaths:
    """Each git call in the transfer reports the failure its caller declared.

    Four call sites pass four different exception types, so a helper that
    raised its own would report a fetch failure during a push and send the
    reader to the wrong half of the system.
    """

    async def test_a_failing_bundle_create_raises_the_declared_type(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scripted = _ScriptedGit([_HEAD, GitBackendPushError("bundle refused")])
        monkeypatch.setattr(_ref_transfer, "git", scripted)
        monkeypatch.setattr(_ref_transfer, "_ref_sha", _no_such_ref)
        with pytest.raises(GitBackendPushError, match="bundle refused"):
            await transfer_ref_local(
                source_root=tmp_path,
                target_git_dir=tmp_path / "bare.git",
                source_ref="feature",
                target_ref="refs/heads/feature",
                cmd_timeout=5.0,
                failure=GitFailure(
                    exc=GitBackendPushError, project_id=_PROJECT, event="e"
                ),
            )
        assert scripted.argv[1][0] == "bundle"

    async def test_a_failing_fetch_raises_the_declared_type(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scripted = _ScriptedGit(
            [_HEAD, "", GitBackendFetchError("target rejected the bundle")]
        )
        monkeypatch.setattr(_ref_transfer, "git", scripted)
        monkeypatch.setattr(_ref_transfer, "_ref_sha", _no_such_ref)
        with pytest.raises(GitBackendFetchError, match="target rejected"):
            await transfer_ref_local(
                source_root=tmp_path,
                target_git_dir=tmp_path / "clone/.git",
                source_ref="refs/heads/feature",
                target_ref="refs/remotes/origin/feature",
                cmd_timeout=5.0,
                failure=GitFailure(
                    exc=GitBackendFetchError, project_id=_PROJECT, event="e"
                ),
            )
        assert scripted.argv[2][1] == "fetch"

    async def test_an_unchanged_ref_sends_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``git bundle create`` refuses an empty range.

        So asking for one would turn "nothing to send" into a failed
        transfer, which is the shape that makes a no-op look like an outage.
        """
        scripted = _ScriptedGit([_HEAD])
        monkeypatch.setattr(_ref_transfer, "git", scripted)

        async def _already_there(*_args: object, **_kwargs: object) -> str:
            return _HEAD

        monkeypatch.setattr(_ref_transfer, "_ref_sha", _already_there)
        await transfer_ref_local(
            source_root=tmp_path,
            target_git_dir=tmp_path / "bare.git",
            source_ref="feature",
            target_ref="refs/heads/feature",
            cmd_timeout=5.0,
            failure=GitFailure(exc=GitBackendPushError, project_id=_PROJECT, event="e"),
        )
        assert [argv[0] for argv in scripted.argv] == ["rev-parse"]

    @pytest.mark.parametrize("ref", ["--upload-pack=evil", "-x"])
    async def test_an_option_like_ref_is_refused_before_git_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ref: str
    ) -> None:
        """Every caller pre-validates today; this module cannot rely on that."""
        scripted = _ScriptedGit([_HEAD])
        monkeypatch.setattr(_ref_transfer, "git", scripted)
        with pytest.raises(ValueError, match="option-like"):
            await transfer_ref_local(
                source_root=tmp_path,
                target_git_dir=tmp_path / "bare.git",
                source_ref=ref,
                target_ref="refs/heads/feature",
                cmd_timeout=5.0,
                failure=GitFailure(
                    exc=GitBackendPushError, project_id=_PROJECT, event="e"
                ),
            )
        assert scripted.argv == []


async def _no_such_ref(*_args: object, **_kwargs: object) -> str | None:
    """The target does not hold the ref, so the whole history is sent."""
    return None
