"""Unit tests for the aiodocker-backed devcontainer image builder.

Real builds belong to the integration tier; these pin the parts that
decide what an operator is told: the containment guard, the build-context
tar, and the mapping from what the daemon did to a :class:`BuildFailure`.
"""

import asyncio
import tarfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import aiodocker
import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import EnvironmentConfigError
from synthorg.engine.workspace.environment.image_builder import (
    AiodockerImageBuilder,
    BuildFailure,
)

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


class _FakeImages:
    """Stands in for ``aiodocker.Docker().images`` over one build."""

    def __init__(self, chunks: list[dict[str, Any]] | BaseException) -> None:
        self._chunks = chunks
        self.kwargs: dict[str, Any] = {}

    def build(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        self.kwargs = kwargs
        chunks = self._chunks

        async def _stream() -> AsyncIterator[dict[str, Any]]:
            if isinstance(chunks, BaseException):
                raise chunks
            for chunk in chunks:
                yield chunk

        return _stream()


class _FakeDocker:
    """Minimal ``aiodocker.Docker`` double: version probe plus images."""

    def __init__(self, chunks: list[dict[str, Any]] | BaseException) -> None:
        self.images = _FakeImages(chunks)
        self.closed = False

    async def version(self) -> dict[str, str]:
        return {"Version": "test"}

    async def close(self) -> None:
        self.closed = True


def _patch_client(client: object) -> Any:
    return patch.object(aiodocker, "Docker", return_value=client)


class TestContainmentGuard:
    def test_dockerfile_outside_the_context_is_refused(self, tmp_path: Path) -> None:
        context, _ = _context(tmp_path)
        outside = tmp_path / "Dockerfile"
        outside.write_text("FROM scratch\n", encoding="utf-8")

        with pytest.raises(EnvironmentConfigError, match="outside the build"):
            AiodockerImageBuilder._assert_contained(outside, context)

    def test_contained_dockerfile_resolves_relative_to_the_context(
        self, tmp_path: Path
    ) -> None:
        """The daemon is given the context-relative path, never an absolute one."""
        context, dockerfile = _context(tmp_path)

        relative = AiodockerImageBuilder._assert_contained(dockerfile, context)

        assert relative == Path("Dockerfile")


class TestContextTar:
    def test_context_is_packed_with_relative_names(self, tmp_path: Path) -> None:
        """A host-absolute member name would not resolve inside the daemon."""
        context, _ = _context(tmp_path)

        stream = AiodockerImageBuilder._context_tar(context)

        with tarfile.open(fileobj=stream, mode="r:gz") as archive:
            names = sorted(archive.getnames())
        assert "./Dockerfile" in names
        assert "./app.txt" in names
        assert not any(name.startswith("/") for name in names)


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

    async def test_timeout_is_distinct_from_a_failed_build(
        self, tmp_path: Path
    ) -> None:
        context, dockerfile = _context(tmp_path)

        async def _hang() -> AsyncIterator[dict[str, Any]]:
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

    async def test_client_is_closed_when_the_caller_cancels(
        self, tmp_path: Path
    ) -> None:
        """A cancelled provision must not leave the build connection open."""
        context, dockerfile = _context(tmp_path)

        async def _hang() -> AsyncIterator[dict[str, Any]]:
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
