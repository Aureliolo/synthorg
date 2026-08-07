"""Unit tests for the aiodocker-backed devcontainer image builder.

Real builds belong to the integration tier; these pin the parts that
decide what an operator is told: the ceiling read per build, and the
mapping from what the daemon did to a :class:`BuildFailure`. What the
build context carries is ``test_context.py``.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractContextManager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiodocker
import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.environment.image_builder import (
    AiodockerImageBuilder,
    BuildFailure,
)
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_TAG = NotBlankStr("synthorg-project-test:abc123")


def _context(root: Path) -> tuple[Path, Path]:
    """Write a minimal build context and return ``(context_dir, dockerfile)``."""
    context = root / "ctx"
    context.mkdir()
    dockerfile = context / "Dockerfile"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    (context / "app.txt").write_text("payload\n", encoding="utf-8")
    return context, dockerfile


# One streamed build chunk. Nested because ``errorDetail`` carries a mapping,
# which is the shape the classifier has to see through.
type _Chunk = dict[str, object]


class _FakeImages:
    """Stands in for ``aiodocker.Docker().images`` over one build."""

    def __init__(self, chunks: list[_Chunk] | BaseException) -> None:
        self._chunks = chunks
        self.kwargs: dict[str, object] = {}

    def build(self, **kwargs: object) -> AsyncIterator[_Chunk]:
        self.kwargs = kwargs
        chunks = self._chunks

        async def _stream() -> AsyncIterator[_Chunk]:
            if isinstance(chunks, BaseException):
                raise chunks
            for chunk in chunks:
                yield chunk

        return _stream()


class _FakeDocker:
    """Minimal ``aiodocker.Docker`` double: version probe plus images."""

    def __init__(self, chunks: list[_Chunk] | BaseException) -> None:
        self.images = _FakeImages(chunks)
        self.closed = False
        self.close_error: BaseException | None = None

    async def version(self) -> dict[str, str]:
        return {"Version": "test"}

    async def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


def _patch_client(client: object) -> AbstractContextManager[object]:
    return patch.object(aiodocker, "Docker", return_value=client)


class TestContextCeiling:
    async def test_the_ceiling_is_read_live_per_build(self) -> None:
        """An operator raising it must not need a restart."""
        resolver = mock_of[ConfigResolverProtocol](
            get_int=AsyncMock(spec=ConfigResolverProtocol.get_int, return_value=99)
        )

        ceiling = await AiodockerImageBuilder(resolver)._context_ceiling()

        assert ceiling == 99
        assert resolver.get_int.await_args.args == (
            "coordination",
            "devcontainer_context_max_bytes",
        )

    async def test_without_a_resolver_the_registered_default_applies(self) -> None:
        """The same value an unset setting resolves to, not a second opinion."""
        ceiling = await AiodockerImageBuilder()._context_ceiling()

        assert ceiling > 0


class TestBuild:
    async def test_successful_build_carries_the_daemon_log(
        self, tmp_path: Path
    ) -> None:
        context, dockerfile = _context(tmp_path)
        client = _FakeDocker([{"stream": "Step 1/1\n"}, {"stream": "done\n"}])

        with _patch_client(client):
            outcome = await AiodockerImageBuilder().build(
                tag=_TAG, dockerfile=dockerfile, context_dir=context, timeout=30.0
            )

        assert outcome.success is True
        assert "Step 1/1" in outcome.log
        assert client.closed is True

    async def test_daemon_error_entry_fails_the_build(self, tmp_path: Path) -> None:
        """The daemon reports a failed build in-band, not by raising."""
        context, dockerfile = _context(tmp_path)
        client = _FakeDocker(
            [{"stream": "Step 1/1\n"}, {"error": "invalid instruction FRM"}]
        )

        with _patch_client(client):
            outcome = await AiodockerImageBuilder().build(
                tag=_TAG, dockerfile=dockerfile, context_dir=context, timeout=30.0
            )

        assert outcome.failure is BuildFailure.BUILD_FAILED
        assert "invalid instruction" in outcome.log

    async def test_an_error_detail_only_entry_also_fails_the_build(
        self, tmp_path: Path
    ) -> None:
        """The richer shape carries no ``error`` key, and it is still a failure."""
        context, dockerfile = _context(tmp_path)
        client = _FakeDocker(
            [
                {"stream": "Step 1/2\n"},
                {"errorDetail": {"code": 1, "message": "returned a non-zero code: 1"}},
            ]
        )

        with _patch_client(client):
            outcome = await AiodockerImageBuilder().build(
                tag=_TAG, dockerfile=dockerfile, context_dir=context, timeout=30.0
            )

        assert outcome.failure is BuildFailure.BUILD_FAILED
        assert "non-zero code" in outcome.log

    async def test_a_run_step_printing_error_is_not_a_failed_build(
        self, tmp_path: Path
    ) -> None:
        """A linter's own stdout is not the daemon's verdict on the build."""
        context, dockerfile = _context(tmp_path)
        client = _FakeDocker(
            [{"stream": "ERROR: 1 vulnerability found, continuing\n"}, {"stream": "ok"}]
        )

        with _patch_client(client):
            outcome = await AiodockerImageBuilder().build(
                tag=_TAG, dockerfile=dockerfile, context_dir=context, timeout=30.0
            )

        assert outcome.success is True

    async def test_a_stream_that_dies_mid_build_is_a_failed_build(
        self, tmp_path: Path
    ) -> None:
        """The transport dropping must not escape as a 500 from provisioning."""
        context, dockerfile = _context(tmp_path)
        client = _FakeDocker(aiodocker.DockerError(500, "stream closed"))

        with _patch_client(client):
            outcome = await AiodockerImageBuilder().build(
                tag=_TAG, dockerfile=dockerfile, context_dir=context, timeout=30.0
            )

        assert outcome.failure is BuildFailure.BUILD_FAILED
        assert client.closed is True

    async def test_an_oversized_context_is_its_own_failure(
        self, tmp_path: Path
    ) -> None:
        """Its own member because a retry would pack the same workspace."""
        context, dockerfile = _context(tmp_path)
        resolver = mock_of[ConfigResolverProtocol](
            get_int=AsyncMock(spec=ConfigResolverProtocol.get_int, return_value=1)
        )
        client = _FakeDocker([])

        with _patch_client(client):
            outcome = await AiodockerImageBuilder(resolver).build(
                tag=_TAG, dockerfile=dockerfile, context_dir=context, timeout=30.0
            )

        assert outcome.failure is BuildFailure.CONTEXT_TOO_LARGE
        assert client.closed is True

    async def test_unreachable_daemon_is_its_own_failure(self, tmp_path: Path) -> None:
        """An unreachable daemon must not read as a Dockerfile the daemon rejected."""
        context, dockerfile = _context(tmp_path)
        client = _FakeDocker([])
        client.version = AsyncMock(  # type: ignore[method-assign]
            side_effect=aiodocker.DockerError(500, "socket missing")
        )

        with _patch_client(client):
            outcome = await AiodockerImageBuilder().build(
                tag=_TAG, dockerfile=dockerfile, context_dir=context, timeout=30.0
            )

        assert outcome.failure is BuildFailure.DAEMON_UNAVAILABLE
        # Closed even though the build never started, so the probe's
        # connection is not leaked.
        assert client.closed is True

    async def test_a_hung_handshake_is_bounded_by_the_callers_timeout(
        self, tmp_path: Path
    ) -> None:
        """A socket that accepts and never answers would otherwise hang the run."""
        context, dockerfile = _context(tmp_path)

        async def _never_answers() -> dict[str, str]:
            await asyncio.Event().wait()
            return {}  # pragma: no cover -- unreachable, the wait never returns

        client = _FakeDocker([])
        client.version = _never_answers  # type: ignore[method-assign]

        with _patch_client(client):
            outcome = await AiodockerImageBuilder().build(
                tag=_TAG, dockerfile=dockerfile, context_dir=context, timeout=0.05
            )

        assert outcome.failure is BuildFailure.DAEMON_UNAVAILABLE
        assert client.closed is True

    async def test_timeout_is_distinct_from_a_failed_build(
        self, tmp_path: Path
    ) -> None:
        context, dockerfile = _context(tmp_path)

        async def _hang() -> AsyncIterator[_Chunk]:
            await asyncio.Event().wait()
            yield {}  # pragma: no cover -- unreachable, the wait never returns

        client = _FakeDocker([])
        client.images.build = lambda **kwargs: _hang()  # type: ignore[method-assign]

        with _patch_client(client):
            outcome = await AiodockerImageBuilder().build(
                tag=_TAG, dockerfile=dockerfile, context_dir=context, timeout=0.05
            )

        assert outcome.failure is BuildFailure.TIMED_OUT
        assert client.closed is True

    async def test_a_failing_close_does_not_replace_the_outcome(
        self, tmp_path: Path
    ) -> None:
        """The teardown runs in a ``finally``; raising there would mask the verdict."""
        context, dockerfile = _context(tmp_path)
        client = _FakeDocker([{"error": "invalid instruction FRM"}])
        client.close_error = aiodocker.DockerError(500, "connector already gone")

        with _patch_client(client):
            outcome = await AiodockerImageBuilder().build(
                tag=_TAG, dockerfile=dockerfile, context_dir=context, timeout=30.0
            )

        assert outcome.failure is BuildFailure.BUILD_FAILED
        assert "invalid instruction" in outcome.log

    async def test_client_is_closed_when_the_caller_cancels(
        self, tmp_path: Path
    ) -> None:
        """A cancelled provision must not leave the build connection open."""
        context, dockerfile = _context(tmp_path)

        async def _hang() -> AsyncIterator[_Chunk]:
            await asyncio.Event().wait()
            yield {}  # pragma: no cover -- unreachable, the wait never returns

        client = _FakeDocker([])
        client.images.build = lambda **kwargs: _hang()  # type: ignore[method-assign]

        with _patch_client(client):
            task = asyncio.create_task(
                AiodockerImageBuilder().build(
                    tag=_TAG,
                    dockerfile=dockerfile,
                    context_dir=context,
                    timeout=30.0,
                )
            )
            # Let the build reach the stream before cancelling it.
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert client.closed is True

    async def test_the_daemon_gets_the_relative_dockerfile_and_a_gzip_context(
        self, tmp_path: Path
    ) -> None:
        context, dockerfile = _context(tmp_path)
        client = _FakeDocker([{"stream": "done\n"}])

        with _patch_client(client):
            await AiodockerImageBuilder().build(
                tag=_TAG, dockerfile=dockerfile, context_dir=context, timeout=30.0
            )

        assert client.images.kwargs["path_dockerfile"] == "Dockerfile"
        assert client.images.kwargs["encoding"] == "gzip"
        assert client.images.kwargs["tag"] == str(_TAG)
